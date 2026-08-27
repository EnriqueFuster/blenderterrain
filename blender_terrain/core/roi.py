"""Validated region-of-interest geometry in WGS84 longitude and latitude."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from ..errors import UserInputError


@dataclass(frozen=True, slots=True)
class BBoxWGS84:
    """A non-wrapping WGS84 bounding box in longitude-latitude order."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        coordinates = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in coordinates):
            raise UserInputError("WGS84 bounds must contain only finite coordinates")
        if not -180.0 <= self.west <= 180.0 or not -180.0 <= self.east <= 180.0:
            raise UserInputError("WGS84 longitude must be between -180 and 180 degrees")
        if not -90.0 <= self.south <= 90.0 or not -90.0 <= self.north <= 90.0:
            raise UserInputError("WGS84 latitude must be between -90 and 90 degrees")
        if self.east <= self.west or self.north <= self.south:
            raise UserInputError("WGS84 bounds must have positive width and height")

    @property
    def longitude_span(self) -> float:
        """Return the longitude span in degrees."""

        return self.east - self.west

    @property
    def latitude_span(self) -> float:
        """Return the latitude span in degrees."""

        return self.north - self.south

    def polygon_ring(self) -> tuple[tuple[float, float], ...]:
        """Return a closed counterclockwise exterior ring."""

        return (
            (self.west, self.south),
            (self.east, self.south),
            (self.east, self.north),
            (self.west, self.north),
            (self.west, self.south),
        )


CoordinateWGS84 = tuple[float, float]
LinearRingWGS84 = tuple[CoordinateWGS84, ...]


@dataclass(frozen=True, slots=True)
class PolygonWGS84:
    """A valid WGS84 polygon with one exterior and optional holes."""

    exterior: LinearRingWGS84
    holes: tuple[LinearRingWGS84, ...] = ()

    def __post_init__(self) -> None:
        _validate_ring(self.exterior, "Polygon exterior")
        for index, hole in enumerate(self.holes, start=1):
            _validate_ring(hole, f"Polygon hole {index}")
            if _rings_intersect(self.exterior, hole):
                raise UserInputError(f"Polygon hole {index} touches or crosses the exterior")
            if not _point_in_ring(hole[0], self.exterior):
                raise UserInputError(f"Polygon hole {index} lies outside the exterior")

        for left_index, left in enumerate(self.holes):
            for right_index, right in enumerate(self.holes[left_index + 1 :], start=left_index + 1):
                if (
                    _rings_intersect(left, right)
                    or _point_in_ring(left[0], right)
                    or _point_in_ring(right[0], left)
                ):
                    raise UserInputError(
                        f"Polygon holes {left_index + 1} and {right_index + 1} overlap"
                    )

    @property
    def vertex_count(self) -> int:
        """Return the number of stored vertices, including ring closures."""

        return len(self.exterior) + sum(len(hole) for hole in self.holes)

    def coordinates(self) -> tuple[LinearRingWGS84, ...]:
        """Return rings in GeoJSON coordinate order."""

        return (self.exterior, *self.holes)


@dataclass(frozen=True, slots=True)
class RegionOfInterest:
    """A portable Polygon or MultiPolygon region of interest in WGS84."""

    polygons: tuple[PolygonWGS84, ...]

    def __post_init__(self) -> None:
        if not self.polygons:
            raise UserInputError("A region of interest must contain at least one polygon")
        if sum(polygon.vertex_count for polygon in self.polygons) > 1_000_000:
            raise UserInputError("The region of interest exceeds the one-million-vertex limit")
        for left_index, left in enumerate(self.polygons):
            for right_index, right in enumerate(
                self.polygons[left_index + 1 :], start=left_index + 1
            ):
                if _polygons_overlap(left, right):
                    raise UserInputError(
                        f"Polygon parts {left_index + 1} and {right_index + 1} overlap"
                    )

    @classmethod
    def from_bbox(cls, bounds: BBoxWGS84) -> RegionOfInterest:
        """Create a rectangular region from validated bounds."""

        return cls((PolygonWGS84(bounds.polygon_ring()),))

    @property
    def bounds(self) -> BBoxWGS84:
        """Return the smallest bounding box containing all polygon parts."""

        points = (point for polygon in self.polygons for point in polygon.exterior[:-1])
        first = next(points)
        west = east = first[0]
        south = north = first[1]
        for longitude, latitude in points:
            west = min(west, longitude)
            east = max(east, longitude)
            south = min(south, latitude)
            north = max(north, latitude)
        return BBoxWGS84(west=west, south=south, east=east, north=north)

    @property
    def geometry_type(self) -> str:
        """Return the corresponding GeoJSON geometry type."""

        return "Polygon" if len(self.polygons) == 1 else "MultiPolygon"

    def to_geojson_geometry(self) -> dict[str, Any]:
        """Serialize the region as a GeoJSON geometry object."""

        polygon_coordinates = [
            [[list(point) for point in ring] for ring in polygon.coordinates()]
            for polygon in self.polygons
        ]
        coordinates: Any = (
            polygon_coordinates[0] if self.geometry_type == "Polygon" else polygon_coordinates
        )
        return {"type": self.geometry_type, "coordinates": coordinates}

    @classmethod
    def from_geojson_geometry(cls, geometry: object) -> RegionOfInterest:
        """Deserialize a strict GeoJSON Polygon or MultiPolygon geometry object."""

        if not isinstance(geometry, dict):
            raise UserInputError("ROI geometry must be a JSON object")
        geometry_type = geometry.get("type")
        if geometry_type not in {"Polygon", "MultiPolygon"}:
            raise UserInputError("ROI geometry must be a Polygon or MultiPolygon")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list):
            raise UserInputError("ROI geometry coordinates must be an array")
        raw_polygons = [coordinates] if geometry_type == "Polygon" else coordinates
        polygons: list[PolygonWGS84] = []
        for raw_polygon in raw_polygons:
            if not isinstance(raw_polygon, list) or not raw_polygon:
                raise UserInputError("ROI polygon must contain an exterior ring")
            rings: list[LinearRingWGS84] = []
            for raw_ring in raw_polygon:
                if not isinstance(raw_ring, list):
                    raise UserInputError("ROI polygon ring must be an array")
                rings.append(closed_ring(raw_ring))
            polygons.append(PolygonWGS84(rings[0], tuple(rings[1:])))
        return cls(tuple(polygons))


def closed_ring(coordinates: Iterable[Iterable[float]]) -> LinearRingWGS84:
    """Convert coordinate pairs to an explicitly closed immutable ring."""

    points: list[CoordinateWGS84] = []
    for index, coordinate in enumerate(coordinates, start=1):
        values = tuple(coordinate)
        if len(values) < 2:
            raise UserInputError(f"Ring coordinate {index} must contain longitude and latitude")
        try:
            point = (float(values[0]), float(values[1]))
        except (TypeError, ValueError) as error:
            raise UserInputError(f"Ring coordinate {index} is not numeric") from error
        points.append(point)
    if points and points[0] != points[-1]:
        points.append(points[0])
    return tuple(points)


def _validate_ring(ring: LinearRingWGS84, label: str) -> None:
    if ring and ring[0] != ring[-1]:
        raise UserInputError(f"{label} must be closed")
    if len(ring) < 4:
        raise UserInputError(f"{label} must contain at least three vertices")
    for longitude, latitude in ring:
        if not math.isfinite(longitude) or not math.isfinite(latitude):
            raise UserInputError(f"{label} contains a non-finite coordinate")
        if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
            raise UserInputError(f"{label} contains a coordinate outside WGS84 bounds")
    if _ring_self_intersects(ring):
        raise UserInputError(f"{label} intersects itself")
    if len(set(ring[:-1])) < 3 or math.isclose(_signed_area(ring), 0.0, abs_tol=1e-15):
        raise UserInputError(f"{label} has zero area")


def _signed_area(ring: LinearRingWGS84) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in pairwise(ring)
    )


def _ring_self_intersects(ring: LinearRingWGS84) -> bool:
    edge_count = len(ring) - 1
    for left_index in range(edge_count):
        for right_index in range(left_index + 1, edge_count):
            if right_index in {left_index, left_index + 1}:
                continue
            if left_index == 0 and right_index == edge_count - 1:
                continue
            if _segments_intersect(
                ring[left_index],
                ring[left_index + 1],
                ring[right_index],
                ring[right_index + 1],
            ):
                return True
    return False


def _rings_intersect(left: LinearRingWGS84, right: LinearRingWGS84) -> bool:
    return any(
        _segments_intersect(left_a, left_b, right_a, right_b)
        for left_a, left_b in pairwise(left)
        for right_a, right_b in pairwise(right)
    )


def _segments_intersect(
    a: CoordinateWGS84,
    b: CoordinateWGS84,
    c: CoordinateWGS84,
    d: CoordinateWGS84,
) -> bool:
    def orientation(p: CoordinateWGS84, q: CoordinateWGS84, r: CoordinateWGS84) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_segment(p: CoordinateWGS84, q: CoordinateWGS84, r: CoordinateWGS84) -> bool:
        return (
            min(p[0], r[0]) - 1e-12 <= q[0] <= max(p[0], r[0]) + 1e-12
            and min(p[1], r[1]) - 1e-12 <= q[1] <= max(p[1], r[1]) + 1e-12
        )

    values = (
        orientation(a, b, c),
        orientation(a, b, d),
        orientation(c, d, a),
        orientation(c, d, b),
    )
    if (values[0] > 0) != (values[1] > 0) and (values[2] > 0) != (values[3] > 0):
        return True
    return any(
        math.isclose(value, 0.0, abs_tol=1e-12) and on_segment(start, point, end)
        for value, start, point, end in (
            (values[0], a, c, b),
            (values[1], a, d, b),
            (values[2], c, a, d),
            (values[3], c, b, d),
        )
    )


def _point_in_ring(point: CoordinateWGS84, ring: LinearRingWGS84) -> bool:
    inside = False
    x, y = point
    for left, right in pairwise(ring):
        if (left[1] > y) != (right[1] > y):
            crossing_x = (right[0] - left[0]) * (y - left[1]) / (right[1] - left[1]) + left[0]
            if x < crossing_x:
                inside = not inside
    return inside


def _polygons_overlap(left: PolygonWGS84, right: PolygonWGS84) -> bool:
    return (
        _rings_intersect(left.exterior, right.exterior)
        or _point_in_ring(left.exterior[0], right.exterior)
        or _point_in_ring(right.exterior[0], left.exterior)
    )
