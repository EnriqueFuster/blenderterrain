"""Blender operators that delegate ROI validation to the portable core."""

from __future__ import annotations

import bpy

from ..core import BBoxWGS84, create_import_plan
from ..errors import BlenderTerrainError
from ..models import DatasetProduct
from . import job_controller


class BLENDERTERRAIN_OT_validate_roi(bpy.types.Operator):
    """Validate manual WGS84 bounds and calculate an offline estimate."""

    bl_idname = "blender_terrain.validate_roi"
    bl_label = "Validate ROI"
    bl_description = "Validate the bounding box without downloading data"

    def execute(self, context: bpy.types.Context) -> set[str]:
        """Validate scene properties through the portable domain layer."""

        properties = context.scene.blender_terrain_roi
        try:
            bounds = BBoxWGS84(
                properties.west,
                properties.south,
                properties.east,
                properties.north,
            )
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


class BLENDERTERRAIN_OT_discover_sources(bpy.types.Operator):
    """Launch source discovery without blocking Blender's interface."""

    bl_idname = "blender_terrain.discover_sources"
    bl_label = "Discover Sources"
    bl_description = "Find the official CNIG elevation files needed for this area"

    def execute(self, context: bpy.types.Context) -> set[str]:
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
