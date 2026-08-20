"""Scene properties used to enter and validate a rectangular ROI."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty


def _invalidate_validation(properties: object, context: bpy.types.Context) -> None:
    """Mark estimates stale whenever an input option changes."""

    properties.is_valid = False
    properties.validation_message = "Options changed; validate ROI again"
    properties.discovery_summary = ""


class BLENDERTERRAIN_ROIProperties(bpy.types.PropertyGroup):
    """Store manual WGS84 bounds and their latest validation result."""

    west: FloatProperty(
        name="West", default=-0.39, min=-180.0, max=180.0, precision=6,
        update=_invalidate_validation,
    )
    south: FloatProperty(
        name="South", default=39.46, min=-90.0, max=90.0, precision=6,
        update=_invalidate_validation,
    )
    east: FloatProperty(
        name="East", default=-0.37, min=-180.0, max=180.0, precision=6,
        update=_invalidate_validation,
    )
    north: FloatProperty(
        name="North", default=39.48, min=-90.0, max=90.0, precision=6,
        update=_invalidate_validation,
    )
    product: EnumProperty(
        name="Elevation Product",
        items=(("MDT02", "DTM (MDT02)", "Bare-earth terrain"),
               ("MDS02", "DSM (MDS02)", "Terrain, buildings and vegetation")),
        default="MDT02",
        update=_invalidate_validation,
    )
    elevation_resolution: EnumProperty(
        name="Elevation Resolution",
        items=(
            ("AUTO", "Auto", "Choose the finest safe resolution"),
            *tuple(
                (str(value), f"{value} m", "Output grid spacing")
                for value in (2, 5, 10, 20, 50, 100)
            ),
        ),
        default="AUTO",
        update=_invalidate_validation,
    )
    use_imagery: BoolProperty(
        name="Use PNOA Orthophoto", default=True, update=_invalidate_validation
    )
    imagery_gsd: EnumProperty(
        name="Imagery GSD",
        items=(
            ("AUTO", "Auto", "Choose the finest safe GSD"),
            *tuple(
                (str(value), f"{value} m", "Texture ground sample distance")
                for value in (0.25, 0.5, 1, 2, 5)
            ),
        ),
        default="AUTO",
        update=_invalidate_validation,
    )

    is_valid: BoolProperty(default=False, options={"HIDDEN"})
    validation_message: StringProperty(default="ROI has not been validated", options={"HIDDEN"})
    crs_summary: StringProperty(default="", options={"HIDDEN"})
    area_square_metres: FloatProperty(default=0.0, options={"HIDDEN"})
    sample_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    selected_resolution: FloatProperty(default=0.0, options={"HIDDEN"})
    imagery_summary: StringProperty(default="", options={"HIDDEN"})
    terrain_tile_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    estimated_memory_mib: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    planning_warning: StringProperty(default="", options={"HIDDEN"})
    job_active: BoolProperty(default=False, options={"HIDDEN"})
    job_state: StringProperty(default="", options={"HIDDEN"})
    job_progress: FloatProperty(
        default=0.0, min=0.0, max=1.0, subtype="FACTOR", options={"HIDDEN"}
    )
    job_message: StringProperty(default="", options={"HIDDEN"})
    discovered_file_count: IntProperty(default=0, min=0, options={"HIDDEN"})
    estimated_download_mb: FloatProperty(default=0.0, min=0.0, options={"HIDDEN"})
    discovery_summary: StringProperty(default="", options={"HIDDEN"})
