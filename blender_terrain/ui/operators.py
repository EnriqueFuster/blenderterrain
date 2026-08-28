"""Blender operators that delegate ROI validation to the portable core."""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from mathutils import Vector

from ..core import (
    RESOURCE_PROFILES,
    BBoxWGS84,
    RegionOfInterest,
    bbox_from_center_size,
    bounds_fully_covered,
    create_import_plan,
    format_bbox,
    inspect_local_elevation,
    inspect_local_imagery,
    parse_bbox,
    resolve_local_elevation_paths,
    subdivision_risk_message,
)
from ..core.cache_inventory import clear_cache, inspect_cache
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


class BLENDERTERRAIN_OT_refresh_cache(bpy.types.Operator):
    """Inspect extension-owned cache categories without modifying them."""

    bl_idname = "blender_terrain.refresh_cache"
    bl_label = "Refresh Cache"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        try:
            inventory = inspect_cache(job_controller.configured_cache_directory(context))
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        properties.cache_inventory_json = json.dumps(
            [
                {
                    "name": category.name,
                    "files": category.file_count,
                    "bytes": category.byte_count,
                    "partials": category.partial_file_count,
                }
                for category in inventory.categories
            ]
        )
        properties.cache_inventory_summary = (
            f"{inventory.file_count} file(s), {_format_bytes(inventory.byte_count)}"
        )
        self.report({"INFO"}, "Cache inventory updated")
        return {"FINISHED"}


class BLENDERTERRAIN_OT_clear_cache(bpy.types.Operator):
    """Remove one explicitly selected, regenerable cache category."""

    bl_idname = "blender_terrain.clear_cache"
    bl_label = "Clear Selected Cache"

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Clear BlenderTerrain Cache",
            message="Remove the selected regenerable cache data?",
            confirm_text="Remove",
            icon="TRASH",
        )

    def execute(self, context: bpy.types.Context) -> set[str]:
        if job_controller.has_active_job():
            self.report({"ERROR"}, "Cannot clean the cache while a job is active")
            return {"CANCELLED"}
        properties = context.scene.blender_terrain_roi
        try:
            result = clear_cache(
                job_controller.configured_cache_directory(context),
                properties.cache_cleanup_selection,
            )
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        properties.cache_inventory_json = "[]"
        properties.cache_inventory_summary = "Cache changed; refresh to inspect it"
        self.report(
            {"INFO"},
            f"Removed {result.file_count} file(s), {_format_bytes(result.byte_count)}",
        )
        return {"FINISHED"}


class BLENDERTERRAIN_OT_retry_job(bpy.types.Operator):
    """Retry the last persisted request while reusing valid cached sources."""

    bl_idname = "blender_terrain.retry_job"
    bl_label = "Retry Last Job"

    def execute(self, context: bpy.types.Context) -> set[str]:
        try:
            job_controller.retry_last_job(context)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Previous job restarted in the background")
        return {"FINISHED"}


def _format_bytes(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    raise AssertionError("Unreachable byte unit")


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
        properties.product_availability_json = "[]"
        properties.product_availability_summary = ""
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
            local_inspection = None
            if properties.elevation_source == "LOCAL":
                raw_path = bpy.path.abspath(properties.local_elevation_path)
                local_inspection = inspect_local_elevation(
                    resolve_local_elevation_paths(raw_path)
                )
                bounds = local_inspection.bounds_wgs84
                _store_bounds(properties, bounds)
                properties.roi_geometry_json = json.dumps(
                    RegionOfInterest.from_bbox(bounds).to_geojson_geometry(),
                    separators=(",", ":"),
                )
                if properties.use_local_imagery:
                    local_imagery = inspect_local_imagery(
                        Path(bpy.path.abspath(properties.local_imagery_path))
                    )
                    if any(
                        projected.epsg != local_imagery.bounds.epsg
                        or not bounds_fully_covered(
                            projected, (local_imagery.bounds,)
                        )
                        for projected in local_inspection.projected_bounds
                    ):
                        raise UserInputError(
                            "Local imagery must use the elevation CRS and cover its full extent"
                        )
                    properties.local_imagery_summary = (
                        f"{local_imagery.width:,} x {local_imagery.height:,} px, "
                        f"{local_imagery.gsd_metres:g} m, "
                        f"EPSG:{local_imagery.bounds.epsg}"
                    )
            else:
                bounds = _bounds_from_properties(properties, store_derived=True)
            if (
                properties.elevation_source == "CNIG"
                and _product_availability_status(properties, properties.product)
                == "NO_COVERAGE"
            ):
                raise UserInputError(
                    "The availability check found no coverage for this product and ROI"
                )
            elevation_limit, imagery_limit = RESOURCE_PROFILES[properties.resource_profile]
            plan = create_import_plan(
                bounds=bounds,
                product=DatasetProduct(properties.product),
                elevation_resolution_metres=(
                    None
                    if properties.elevation_resolution == "AUTO"
                    else float(properties.elevation_resolution)
                ),
                use_imagery=(
                    properties.use_imagery and properties.elevation_source == "CNIG"
                ),
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
                maximum_elevation_samples=elevation_limit,
                maximum_imagery_pixels=imagery_limit,
                native_resolution_override=(
                    None
                    if local_inspection is None
                    else local_inspection.native_resolution_metres
                ),
                projected_bounds_override=(
                    None
                    if local_inspection is None
                    else local_inspection.projected_bounds
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
            properties.estimated_base_vertices = 0
            properties.estimated_texture_gpu_mib = 0.0
            properties.planning_warning = ""
            properties.local_elevation_summary = ""
            properties.local_native_resolution = 0.0
            properties.local_imagery_summary = ""
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        properties.is_valid = True
        properties.validation_message = (
            "Local elevation rasters are valid"
            if local_inspection is not None
            else "ROI is valid for offline planning"
        )
        if local_inspection is not None:
            properties.local_native_resolution = (
                local_inspection.native_resolution_metres
            )
            properties.local_elevation_summary = (
                f"{len(local_inspection.paths)} TIFF file(s), "
                f"{local_inspection.native_resolution_metres:g} m, "
                + ", ".join(f"EPSG:{epsg}" for epsg in local_inspection.epsg_codes)
            )
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
        properties.estimated_base_vertices = sum(
            (tile.rows + 1) * (tile.columns + 1) for tile in terrain_tiles
        )
        properties.estimated_texture_gpu_mib = (
            plan.estimated_imagery_decoded_bytes / (1024 * 1024)
        )
        warnings = list(plan.warnings)
        if properties.resource_profile == "LARGE":
            warnings.append("Large profile can exhaust system or GPU memory")
        properties.planning_warning = " | ".join(warnings)
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


class BLENDERTERRAIN_OT_check_product_availability(bpy.types.Operator):
    """Check every official elevation product for the current ROI."""

    bl_idname = "blender_terrain.check_product_availability"
    bl_label = "Check Product Availability"
    bl_description = "Check which CNIG elevation products cover the current ROI"

    def execute(self, context: bpy.types.Context) -> set[str]:
        properties = context.scene.blender_terrain_roi
        if not properties.roi_geometry_json:
            try:
                validation = bpy.ops.blender_terrain.validate_roi()
            except RuntimeError as exc:
                self.report({"ERROR"}, str(exc).removeprefix("Error: "))
                return {"CANCELLED"}
            if validation != {"FINISHED"}:
                return {"CANCELLED"}
        try:
            job_controller.start_availability(context)
        except BlenderTerrainError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "Product availability check started in the background")
        return {"FINISHED"}


def _product_availability_status(properties: object, product: str) -> str | None:
    try:
        entries = json.loads(properties.product_availability_json)
    except (AttributeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if isinstance(entry, dict) and entry.get("product") == product:
            status = entry.get("status")
            return status if isinstance(status, str) else None
    return None


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
            try:
                validation = bpy.ops.blender_terrain.validate_roi()
            except RuntimeError as exc:
                self.report({"ERROR"}, str(exc).removeprefix("Error: "))
                return {"CANCELLED"}
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
        clip_summary = ""
        if properties.adjust_viewport_clip_end:
            viewport_count, clip_end = _adjust_viewport_clip_end(context, objects)
            if viewport_count:
                clip_summary = f"; Clip End at least {clip_end:g} m"
        self.report({"INFO"}, f"Created {len(objects)} terrain object(s){clip_summary}")
        return {"FINISHED"}


def _adjust_viewport_clip_end(
    context: bpy.types.Context, objects: tuple[bpy.types.Object, ...]
) -> tuple[int, float]:
    """Increase every open 3D viewport clipping distance to fit the terrain."""

    world_corners = [
        object_.matrix_world @ Vector(corner)
        for object_ in objects
        for corner in object_.bound_box
    ]
    if not world_corners:
        return 0, 0.0
    extents = tuple(
        max(corner[axis] for corner in world_corners)
        - min(corner[axis] for corner in world_corners)
        for axis in range(3)
    )
    target = max(1_000.0, 2.0 * sum(value * value for value in extents) ** 0.5)
    updated = 0
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.clip_end = max(space.clip_end, target)
                    updated += 1
    return updated, target


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
    """Pack external images used by the current terrain into the blend file."""

    bl_idname = "blender_terrain.pack_imagery"
    bl_label = "Pack Terrain Images"
    bl_description = "Store copies of this terrain's external images inside the blend file"

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event) -> set[str]:
        size = context.scene.blender_terrain_roi.imagery_size_mib
        return context.window_manager.invoke_confirm(
            self,
            event,
            title="Pack Terrain Images",
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
            self.report({"ERROR"}, f"Cannot pack terrain images: {exc}")
            return {"CANCELLED"}
        if not images:
            self.report({"WARNING"}, "This terrain has no external images to pack")
            return {"CANCELLED"}
        properties.imagery_packed = True
        self.report({"INFO"}, f"Packed {len(images)} terrain image(s) into the blend file")
        return {"FINISHED"}
