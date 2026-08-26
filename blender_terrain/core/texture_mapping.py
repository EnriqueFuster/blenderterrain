"""Projected texture transforms for PNOA imagery tiles."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise

from ..models import ProjectedBounds


@dataclass(frozen=True, slots=True)
class TextureTransform:
    """Map normalized terrain XY coordinates into one imagery tile."""

    scale_u: float
    scale_v: float
    offset_u: float
    offset_v: float


def projected_texture_transform(
    terrain: ProjectedBounds, imagery: ProjectedBounds
) -> TextureTransform | None:
    """Return a normalized XY transform, or None when coverage does not intersect."""

    if terrain.epsg != imagery.epsg:
        return None
    if (
        imagery.east <= terrain.west
        or imagery.west >= terrain.east
        or imagery.north <= terrain.south
        or imagery.south >= terrain.north
    ):
        return None
    imagery_width = imagery.east - imagery.west
    imagery_height = imagery.north - imagery.south
    return TextureTransform(
        scale_u=(terrain.east - terrain.west) / imagery_width,
        scale_v=(terrain.north - terrain.south) / imagery_height,
        offset_u=(terrain.west - imagery.west) / imagery_width,
        offset_v=(terrain.south - imagery.south) / imagery_height,
    )


def bounds_fully_covered(
    terrain: ProjectedBounds, imagery: tuple[ProjectedBounds, ...]
) -> bool:
    """Return whether the union of imagery rectangles covers the terrain rectangle."""

    relevant = tuple(
        bounds
        for bounds in imagery
        if bounds.epsg == terrain.epsg
        and bounds.east > terrain.west
        and bounds.west < terrain.east
        and bounds.north > terrain.south
        and bounds.south < terrain.north
    )
    x_edges = sorted(
        {terrain.west, terrain.east}
        | {
            max(terrain.west, min(terrain.east, edge))
            for bounds in relevant
            for edge in (bounds.west, bounds.east)
        }
    )
    for left, right in pairwise(x_edges):
        if right <= left:
            continue
        midpoint = (left + right) / 2.0
        intervals = sorted(
            (
                max(terrain.south, bounds.south),
                min(terrain.north, bounds.north),
            )
            for bounds in relevant
            if bounds.west <= midpoint <= bounds.east
        )
        covered_to = terrain.south
        for south, north in intervals:
            if south > covered_to:
                return False
            covered_to = max(covered_to, north)
            if covered_to >= terrain.north:
                break
        if covered_to < terrain.north:
            return False
    return len(x_edges) >= 2
