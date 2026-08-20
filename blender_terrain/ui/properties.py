"""Scene properties used to enter and validate a rectangular ROI."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty


class BLENDERTERRAIN_ROIProperties(bpy.types.PropertyGroup):
    """Store manual WGS84 bounds and their latest validation result."""

    west: FloatProperty(name="West", default=-0.39, min=-180.0, max=180.0, precision=6)
    south: FloatProperty(name="South", default=39.46, min=-90.0, max=90.0, precision=6)
    east: FloatProperty(name="East", default=-0.37, min=-180.0, max=180.0, precision=6)
    north: FloatProperty(name="North", default=39.48, min=-90.0, max=90.0, precision=6)

    is_valid: BoolProperty(default=False, options={"HIDDEN"})
    validation_message: StringProperty(default="ROI has not been validated", options={"HIDDEN"})
    crs_summary: StringProperty(default="", options={"HIDDEN"})
    area_square_metres: FloatProperty(default=0.0, options={"HIDDEN"})
    sample_count: IntProperty(default=0, min=0, options={"HIDDEN"})
