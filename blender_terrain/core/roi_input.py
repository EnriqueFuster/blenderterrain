"""User-facing rectangular ROI construction methods."""

from __future__ import annotations

import math
import re

from ..errors import UserInputError
from .crs import ProjectedWorkArea, split_bbox_by_wgs84_utm_zone
from .projection import (
    ProjectedPoint,
    project_utm_to_wgs84,
    project_wgs84_to_utm,
    project_work_area_bounds,
)
from .roi import BBoxWGS84


def bbox_from_center_size(
    longitude: float, latitude: float, width_metres: float, height_metres: float
) -> BBoxWGS84:
    """Build a WGS84 envelope around a projected centre and metric rectangle."""

    values = (longitude, latitude, width_metres, height_metres)
    if not all(math.isfinite(value) for value in values):
        raise UserInputError("ROI centre and dimensions must be finite")
    if width_metres <= 0.0 or height_metres <= 0.0:
        raise UserInputError("ROI width and height must be positive")
    epsilon = 1e-8
    probe = BBoxWGS84(
        longitude - epsilon,
        latitude - epsilon,
        longitude + epsilon,
        latitude + epsilon,
    )
    work_areas = split_bbox_by_wgs84_utm_zone(probe)
    crs = work_areas[-1].crs
    center = project_wgs84_to_utm(longitude, latitude, crs)
    half_width = width_metres / 2.0
    half_height = height_metres / 2.0
    corners = tuple(
        project_utm_to_wgs84(
            ProjectedPoint(center.easting + x, center.northing + y, crs.epsg), crs
        )
        for x in (-half_width, half_width)
        for y in (-half_height, half_height)
    )
    bounds = BBoxWGS84(
        min(point.longitude for point in corners),
        min(point.latitude for point in corners),
        max(point.longitude for point in corners),
        max(point.latitude for point in corners),
    )
    for _iteration in range(8):
        projected = project_work_area_bounds(ProjectedWorkArea(bounds, crs))
        longitude_scale = width_metres / (projected.east - projected.west)
        latitude_scale = height_metres / (projected.north - projected.south)
        bounds = BBoxWGS84(
            longitude + (bounds.west - longitude) * longitude_scale,
            latitude + (bounds.south - latitude) * latitude_scale,
            longitude + (bounds.east - longitude) * longitude_scale,
            latitude + (bounds.north - latitude) * latitude_scale,
        )
    split_bbox_by_wgs84_utm_zone(bounds)
    return bounds


def format_bbox(bounds: BBoxWGS84) -> str:
    """Format a bounding box for clipboard interchange."""

    return ", ".join(
        f"{value:.8f}" for value in (bounds.west, bounds.south, bounds.east, bounds.north)
    )


def parse_bbox(text: str) -> BBoxWGS84:
    """Parse west, south, east and north separated by commas or whitespace."""

    if not isinstance(text, str):
        raise UserInputError("Bounding box clipboard content must be text")
    parts = [part for part in re.split(r"[,;\s]+", text.strip()) if part]
    if len(parts) != 4:
        raise UserInputError("Bounding box must contain west, south, east and north")
    try:
        return BBoxWGS84(*(float(part) for part in parts))
    except ValueError as exc:
        raise UserInputError("Bounding box clipboard content contains invalid numbers") from exc
