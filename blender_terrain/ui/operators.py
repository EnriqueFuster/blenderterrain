"""Blender operators that delegate ROI validation to the portable core."""

from __future__ import annotations

import bpy

from ..core import BBoxWGS84, estimate_bbox, split_bbox_by_utm_zone
from ..errors import BlenderTerrainError


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
            work_areas = split_bbox_by_utm_zone(bounds)
            estimate = estimate_bbox(bounds)
        except BlenderTerrainError as exc:
            properties.is_valid = False
            properties.validation_message = str(exc)
            properties.crs_summary = ""
            properties.area_square_metres = 0.0
            properties.sample_count = 0
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        properties.is_valid = True
        properties.validation_message = "ROI is valid for offline planning"
        properties.crs_summary = ", ".join(f"EPSG:{area.crs.epsg}" for area in work_areas)
        properties.area_square_metres = estimate.area_square_metres
        properties.sample_count = estimate.sample_count
        self.report({"INFO"}, properties.validation_message)
        return {"FINISHED"}
