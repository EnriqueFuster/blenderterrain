"""Blender operators that delegate ROI validation to the portable core."""

from __future__ import annotations

import json
from pathlib import Path

import bpy

from ..core import (
    BBoxWGS84,
    RegionOfInterest,
    bbox_from_center_size,
    create_import_plan,
    format_bbox,
    parse_bbox,
    subdivision_risk_message,
)
from ..errors import BlenderTerrainError, UserInputError
from ..io.roi_files import read_roi_file
from ..io.roi_map_server import ROIMapSession
from ..models import DatasetProduct
from . import job_controller
from .terrain_builder import (
    collection_for_import,
    create_terrain_objects,
    pack_collection_images,
    terrain_import_exists,
)
from .terrain_controls import (
    apply_global_settings,
    apply_selected_settings,
    restore_selected_settings,
    select_import_objects,
    sync_selected_settings,
)

_active_map_session: ROIMapSession | None = None


class BLENDERTERRAIN_OT_open_roi_map(bpy.types.Operator):
    """Open the authenticated browser map and wait without blocking Blender."""

    bl_idname = "blender_terrain.open_roi_map"
    bl_label = "Open Map Selector"
    bl_description = "Open a browser map to draw the area and return it to Blender"

    _timer: object | None = None

    def execute(self, context: bpy.types.Context) -> set[str]:
        global _active_map_session
        properties = context.scene.blender_terrain_roi
        mode = {
            "MAP_RECTANGLE": "RECTANGLE",
            "MAP_POLYGON": "POLYGON",
        }.get(properties.roi_input_mode)
        if mode is None:
            self.report({"ERROR"}, "Choose a map drawing ROI mode first")
            return {"CANCELLED"}
        shutdown_map_selector()
        try:
            bounds = BBoxWGS84(
                properties.west, properties.south, properties.east, properties.north
            )
            _active_map_session = ROIMapSession(mode, bounds)
            url = _active_map_session.start()
            if bpy.ops.wm.url_open(url=url) != {"FINISHED"}:
                raise RuntimeError("Blender could not launch the default browser")
        except (BlenderTerrainError, OSError, RuntimeError) as exc:
            shutdown_map_selector()
            self.report({"ERROR"}, f"Cannot open the ROI map: {exc}")
            return {"CANCELLED"}
        self._timer = context.window_manager.event_timer_add(0.25, window=context.window)
        context.window_manager.modal_handler_add(self)
        properties.validation_message = "Waiting for an area from the browser map"
        self.report({"INFO"}, "ROI map opened in the default browser")
        return {"RUNNING_MODAL"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        session = _active_map_session
        if event.type == "ESC":
            self._finish(context)
            shutdown_map_selector()
            self.report({"INFO"}, "ROI map selection cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER" or session is None or not session.finished.is_set():
            return {"PASS_THROUGH"}
        self._finish(context)
        if session.cancelled:
            shutdown_map_selector()
            self.report({"INFO"}, "ROI map selection cancelled")
            return {"CANCELLED"}
        if session.result is None:
            message = session.error or "The browser returned no ROI geometry"
            shutdown_map_selector()
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        properties = context.scene.blender_terrain_roi
        result = session.result
        shutdown_map_selector()
        _store_bounds(properties, result.bounds)
        properties.roi_geometry_json = json.dumps(
            result.to_geojson_geometry(), separators=(",", ":")
        )
        validation = bpy.ops.blender_terrain.validate_roi()
        if validation != {"FINISHED"}:
            return {"CANCELLED"}
        self.report({"INFO"}, "ROI received and validated from the browser map")
        return {"FINISHED"}

    def cancel(self, context: bpy.types.Context) -> None:
        self._finish(context)
        shutdown_map_selector()

    def _finish(self, context: bpy.types.Context) -> None:
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


def shutdown_map_selector() -> None:
    """Close an active browser-map callback server during cancellation or unload."""

    global _active_map_session
    if _active_map_session is not None:
        _active_map_session.close()
        _active_map_session = None


class BLENDERTERRAIN_OT_validate_roi(bpy.types.Operator):
    """Validate manual WGS84 bounds and calculate an offline estimate."""

    bl_idname = "blender_terrain.validate_roi"
    bl_label = "Validate ROI"
    bl_description = "Validate the bounding box without downloading data"

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Validate scene properties through the portable domain layer."""

        properties = context.scene.blender_terrain_roi
        try:
            bounds = _bounds_from_properties(properties, store_derived=True)
            plan = create_import_plan(
                bounds=bounds,
                product=DatasetProduct(properties.product),
                elevation_resolution_metres=(
                    None
                    if properties.elevation_resolution == "AUTO"
                    else float(properties.elevation_resolution)
                ),
                use_imagery=properties.use_imagery,
                imagery_gsd_metres=(
                    None if properties.imagery_gsd == "AUTO" else float(properties.imagery_gsd)
                ),
                manual_tile_rows=(
                    properties.manual_tile_rows
                    if properties.tiling_mode == "MANUAL"
                    else None
                ),
                manual_tile_columns=(
                    properties.manual_tile_columns
                    if properties.tiling_mode == "MANUAL"
                    else None
                ),
            )
        except BlenderTerrainError as exc:
            properties.is_valid = False
            properties.validation_message = str(exc)
            properties.crs_summary = ""
            properties.area_square_metres = 0.0
            properties.sample_count = 0
            properties.selected_resolution = 0.0
            properties.imagery_summary = ""
            properties.terrain_tile_count = 0
            properties.terrain_tile_summary = ""
            properties.estimated_memory_mib = 0.0
            properties.planning_warning = ""
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        properties.is_valid = True
        properties.validation_message = "ROI is valid for offline planning"
        properties.crs_summary = ", ".join(
            f"EPSG:{area.crs.epsg}" for area in plan.work_areas
        )
        properties.area_square_metres = plan.elevation.area_square_metres
        properties.sample_count = plan.elevation_sample_count
        properties.selected_resolution = plan.elevation_resolution_metres
        properties.terrain_tile_count = plan.terrain_tile_count
        terrain_tiles = tuple(
            tile
            for grid_index in range(len(plan.grids))
            for tile in plan.tiles_for_grid(grid_index)
        )
        largest_tile = max(terrain_tiles, key=lambda tile: tile.sample_count)
        properties.terrain_tile_summary = (
            f"Largest object: {largest_tile.columns} x {largest_tile.rows} cells"
        )
        properties.estimated_memory_mib = plan.estimated_combined_bytes / (1024 * 1024)
        properties.planning_warning = " | ".join(plan.warnings)
        properties.imagery_summary = (
            "PNOA disabled"
            if plan.imagery is None
            else (
                f"PNOA {plan.imagery.gsd_metres:g} m: "
                f"{plan.imagery.pixel_width:,} x {plan.imagery.pixel_height:,} px, "
                f"{plan.imagery.tile_count} provisional tile(s)"
            )
        )
        self.report({"INFO"}, properties.validation_message)
        return {"FINISHED"}


class BLENDERTERRAIN_OT_update_bbox_from_center(bpy.types.Operator):
    """Calculate WGS84 bounds from the current centre and metric dimensions."""

    bl_idname = "blender_terrain.update_bbox_from_center"
    bl_label = "Update Bounding Box"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            bounds = bbox_from_center_size(
                properties.center_longitude,
                properties.center_latitude,
                properties.roi_width_metres,
                properties.roi_height_metres,
            )
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _store_bounds(properties, bounds)
        self.report({"INFO"}, "Bounding box updated from metric dimensions")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_copy_bbox(bpy.types.Operator):
    """Copy the current WGS84 bounds as four comma-separated coordinates."""

    bl_idname = "blender_terrain.copy_bbox"
    bl_label = "Copy BBox"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            bounds = _bounds_from_properties(properties)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        context.window_manager.clipboard = format_bbox(bounds)
        self.report({"INFO"}, "Bounding box copied")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_paste_bbox(bpy.types.Operator):
    """Replace the current WGS84 bounds from clipboard text."""

    bl_idname = "blender_terrain.paste_bbox"
    bl_label = "Paste BBox"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            bounds = parse_bbox(context.window_manager.clipboard)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        properties.roi_input_mode = "BOUNDING_BOX"
        _store_bounds(properties, bounds)
        self.report({"INFO"}, "Bounding box pasted")
        return {"FINISHED"}


def _bounds_from_properties(
    properties: object, *, store_derived: bool = False
) -> BBoxWGS84:
    if properties.roi_input_mode == "FILE":
        path = Path(bpy.path.abspath(properties.roi_file_path))
        layer_name = (
            properties.gpkg_layer
            if path.suffix.lower() == ".gpkg" and properties.gpkg_layer != "__NONE__"
            else None
        )
        region = read_roi_file(path, layer_name)
        if store_derived:
            _store_bounds(properties, region.bounds)
            properties.roi_geometry_json = json.dumps(
                region.to_geojson_geometry(), separators=(",", ":")
            )
        return region.bounds
    if properties.roi_input_mode in {"MAP_RECTANGLE", "MAP_POLYGON"}:
        if not properties.roi_geometry_json:
            raise UserInputError("Open the map and select an area first")
        try:
            serialized = json.loads(properties.roi_geometry_json)
        except json.JSONDecodeError as exc:
            raise UserInputError("Stored map geometry is invalid; select the area again") from exc
        region = RegionOfInterest.from_geojson_geometry(serialized)
        return region.bounds
    if properties.roi_input_mode == "CENTER_SIZE":
        bounds = bbox_from_center_size(
            properties.center_longitude,
            properties.center_latitude,
            properties.roi_width_metres,
            properties.roi_height_metres,
        )
        if store_derived:
            _store_bounds(properties, bounds)
            properties.roi_geometry_json = json.dumps(
                RegionOfInterest.from_bbox(bounds).to_geojson_geometry(),
                separators=(",", ":"),
            )
        return bounds
    bounds = BBoxWGS84(
        properties.west, properties.south, properties.east, properties.north
    )
    if store_derived:
        properties.roi_geometry_json = json.dumps(
            RegionOfInterest.from_bbox(bounds).to_geojson_geometry(),
            separators=(",", ":"),
        )
    return bounds


def _store_bounds(properties: object, bounds: BBoxWGS84) -> None:
    properties.internal_update = True
    try:
        properties.west = bounds.west
        properties.south = bounds.south
        properties.east = bounds.east
        properties.north = bounds.north
    finally:
        properties.internal_update = False


class BLENDERTERRAIN_OT_discover_sources(bpy.types.Operator):
    """Launch source discovery without blocking Blender's interface."""

    bl_idname = "blender_terrain.discover_sources"
    bl_label = "Discover Sources"
    bl_description = "Find the official CNIG elevation files needed for this area"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        if not properties.is_valid:
            validation = bpy.ops.blender_terrain.validate_roi()
            if validation != {"FINISHED"}:
                self.report({"ERROR"}, "Correct the area or data settings before discovery")
                return {"CANCELLED"}
        try:
            job_controller.start_discovery(context)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Source discovery started in the background")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_cancel_discovery(bpy.types.Operator):
    """Request cooperative cancellation of the active background job."""

    bl_idname = "blender_terrain.cancel_discovery"
    bl_label = "Cancel Job"

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            job_controller.cancel_discovery()
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class BLENDERTERRAIN_OT_download_data(bpy.types.Operator):
    """Download discovered elevation and optional PNOA sources."""

    bl_idname = "blender_terrain.download_data"
    bl_label = "Download Data"
    bl_description = "Download and validate the required elevation and imagery files"

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            job_controller.start_delivery(context)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Data download started in the background")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_create_terrain(bpy.types.Operator):
    """Create Blender terrain objects from the completed delivery result."""

    bl_idname = "blender_terrain.create_terrain"
    bl_label = "Create Terrain"
    bl_description = "Create one georeferenced mesh object per processed terrain tile"

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        """Warn before deliberately creating another copy of the same import."""

        import_id = context.scene.blender_terrain_roi.import_id
        if import_id and terrain_import_exists(import_id):
            return context.window_manager.invoke_confirm(
                self,
                event,
                title="Duplicate Terrain Import",
                message=(
                    "This terrain already exists in the scene. "
                    "Create another independent copy?"
                ),
                confirm_text="Create Copy",
                icon="QUESTION",
            )
        return self.execute(context)

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        window_manager = context.window_manager

        def report_progress(progress: float, message: str) -> None:
            window_manager.progress_update(progress * 100.0)
            if context.workspace is not None:
                context.workspace.status_text_set(text=message)

        window_manager.progress_begin(0.0, 100.0)
        try:
            objects = create_terrain_objects(
                context,
                Path(properties.delivery_result_path),
                1.0,
                properties.pack_imagery,
                properties.full_resolution_mesh,
                report_progress,
            )
        except (BlenderTerrainError, OSError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        finally:
            window_manager.progress_end()
            if context.workspace is not None:
                context.workspace.status_text_set(text=None)
        properties.terrain_created = True
        properties.active_import_id = properties.import_id
        properties.imagery_packed = (
            properties.pack_imagery and properties.imagery_available
        )
        self.report({"INFO"}, f"Created {len(objects)} terrain object(s)")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_select_import_objects(bpy.types.Operator):
    """Select all mesh objects belonging to the active terrain import."""

    bl_idname = "blender_terrain.select_import_objects"
    bl_label = "Select Terrain Objects"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            count = select_import_objects(context, properties.active_import_id)
            sync_selected_settings(context, properties, force=True)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Selected {count} terrain object(s)")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_apply_import_settings(bpy.types.Operator):
    """Apply shared displacement settings to the complete active import."""

    bl_idname = "blender_terrain.apply_import_settings"
    bl_label = "Apply to Entire Terrain"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            count = apply_global_settings(properties)
            sync_selected_settings(context, properties, force=True)
        except (BlenderTerrainError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        risk = subdivision_risk_message(
            properties.terrain_subdivision_viewport,
            properties.terrain_subdivision_render,
        )
        self.report(
            {"WARNING"} if risk else {"INFO"},
            risk or f"Updated {count} terrain object(s)",
        )
        return {"FINISHED"}


class BLENDERTERRAIN_OT_apply_selected_settings(bpy.types.Operator):
    """Apply local displacement overrides to selected terrain objects."""

    bl_idname = "blender_terrain.apply_selected_settings"
    bl_label = "Apply to Selected Objects"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            count, seams = apply_selected_settings(context, properties)
            sync_selected_settings(context, properties, force=True)
        except (BlenderTerrainError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        risk = subdivision_risk_message(
            properties.selected_subdivision_viewport,
            properties.selected_subdivision_render,
        )
        if seams or risk:
            warnings = []
            if seams:
                warnings.append(f"{seams} shared edge(s) may be discontinuous")
            if risk:
                warnings.append(risk)
            self.report(
                {"WARNING"},
                f"Updated {count} object(s); " + "; ".join(warnings),
            )
        else:
            self.report({"INFO"}, f"Updated {count} terrain object(s)")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_restore_selected_settings(bpy.types.Operator):
    """Restore selected terrain objects to their import-wide settings."""

    bl_idname = "blender_terrain.restore_selected_settings"
    bl_label = "Restore Selected to Global"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            count = restore_selected_settings(context, properties)
            sync_selected_settings(context, properties, force=True)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Restored {count} terrain object(s)")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_pack_imagery(bpy.types.Operator):
    """Pack cached PNOA images used by the current terrain into the blend file."""

    bl_idname = "blender_terrain.pack_imagery"
    bl_label = "Pack PNOA Images"
    bl_description = "Store copies of this terrain's external PNOA images inside the blend file"

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        size = context.scene.blender_terrain_roi.imagery_size_mib
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Pack PNOA Images",
            message=(
                f"Embed approximately {size:.1f} MiB in the blend file? "
                "The external cache files will be kept."
            ),
            confirm_text="Pack Images",
            icon="PACKAGE",
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        collection = collection_for_import(properties.import_id)
        if collection is None:
            self.report({"ERROR"}, "The current terrain import is not present in the scene")
            return {"CANCELLED"}
        try:
            images = pack_collection_images(collection)
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Cannot pack PNOA images: {exc}")
            return {"CANCELLED"}
        if not images:
            self.report({"WARNING"}, "This terrain has no PNOA images to pack")
            return {"CANCELLED"}
        properties.imagery_packed = True
        self.report({"INFO"}, f"Packed {len(images)} PNOA image(s) into the blend file")
        return {"FINISHED"}
