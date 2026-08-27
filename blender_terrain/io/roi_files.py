"""Read portable polygon ROI files without optional GIS dependencies."""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Callable
from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ..core.crs import CRSInfo
from ..core.projection import ProjectedPoint, project_utm_to_wgs84
from ..core.roi import PolygonWGS84, RegionOfInterest, closed_ring
from ..errors import UserInputError

MAX_ROI_FILE_BYTES = 32 * 1024 * 1024


def read_roi_file(path: str | Path) -> RegionOfInterest:
    """Read a GeoJSON or KML polygon file as a WGS84 region of interest."""

    source = Path(path)
    if not source.is_file():
        raise UserInputError(f"ROI file does not exist: {source}")
    size = source.stat().st_size
    if size == 0:
        raise UserInputError("ROI file is empty")
    if size > MAX_ROI_FILE_BYTES:
        raise UserInputError("ROI file exceeds the 32 MiB safety limit")

    suffix = source.suffix.lower()
    if suffix in {".geojson", ".json"}:
        return read_geojson_roi(source.read_text(encoding="utf-8-sig"))
    if suffix == ".kml":
        return read_kml_roi(source.read_text(encoding="utf-8-sig"))
    if suffix == ".shp":
        return read_shapefile_roi(source)
    raise UserInputError("ROI file must use the .geojson, .json, .kml, or .shp extension")


def read_geojson_roi(text: str) -> RegionOfInterest:
    """Parse Polygon or MultiPolygon geometry from a GeoJSON document."""

    try:
        document = json.loads(text)
    except (json.JSONDecodeError, UnicodeError) as error:
        raise UserInputError(f"Invalid GeoJSON: {error}") from error
    if not isinstance(document, dict):
        raise UserInputError("GeoJSON root must be an object")
    if document.get("crs") is not None:
        raise UserInputError(
            "GeoJSON with a custom CRS is unsupported; export the file as WGS84 (EPSG:4326)"
        )

    polygons: list[PolygonWGS84] = []
    _collect_geojson_polygons(document, polygons)
    if not polygons:
        raise UserInputError("GeoJSON does not contain Polygon or MultiPolygon geometry")
    return RegionOfInterest(tuple(polygons))


def read_kml_roi(text: str) -> RegionOfInterest:
    """Parse all Polygon elements from a WGS84 KML document."""

    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise UserInputError("KML document type and entity declarations are unsupported")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise UserInputError(f"Invalid KML: {error}") from error

    polygons: list[PolygonWGS84] = []
    for element in root.iter():
        if _local_name(element.tag) != "Polygon":
            continue
        exterior: tuple[tuple[float, float], ...] | None = None
        holes: list[tuple[tuple[float, float], ...]] = []
        for boundary in element:
            boundary_name = _local_name(boundary.tag)
            if boundary_name not in {"outerBoundaryIs", "innerBoundaryIs"}:
                continue
            coordinates_text = _first_descendant_text(boundary, "coordinates")
            if coordinates_text is None:
                raise UserInputError(f"KML {boundary_name} has no coordinates")
            ring = closed_ring(_parse_kml_coordinates(coordinates_text))
            if boundary_name == "outerBoundaryIs":
                if exterior is not None:
                    raise UserInputError("KML Polygon contains more than one exterior boundary")
                exterior = ring
            else:
                holes.append(ring)
        if exterior is None:
            raise UserInputError("KML Polygon has no exterior boundary")
        polygons.append(PolygonWGS84(exterior=exterior, holes=tuple(holes)))

    if not polygons:
        raise UserInputError("KML does not contain Polygon geometry")
    return RegionOfInterest(tuple(polygons))


def read_shapefile_roi(path: str | Path) -> RegionOfInterest:
    """Read Polygon records from a Shapefile with an explicit supported .prj file."""

    source = Path(path)
    projection_path = _find_projection_file(source)
    if projection_path is None:
        raise UserInputError("Shapefile ROI requires an accompanying .prj file")
    converter = _shapefile_coordinate_converter(
        projection_path.read_text(encoding="utf-8-sig", errors="replace")
    )
    data = source.read_bytes()
    if len(data) < 100 or struct.unpack_from(">i", data, 0)[0] != 9994:
        raise UserInputError("Invalid Shapefile header")
    if struct.unpack_from("<i", data, 28)[0] != 1000:
        raise UserInputError("Unsupported Shapefile version")
    header_shape_type = struct.unpack_from("<i", data, 32)[0]
    if header_shape_type not in {0, 5, 15, 25}:
        raise UserInputError("Shapefile ROI must contain Polygon geometry")

    polygons: list[PolygonWGS84] = []
    offset = 100
    while offset < len(data):
        if offset + 8 > len(data):
            raise UserInputError("Truncated Shapefile record header")
        content_words = struct.unpack_from(">i", data, offset + 4)[0]
        content_size = content_words * 2
        content_start = offset + 8
        content_end = content_start + content_size
        if content_size < 4 or content_end > len(data):
            raise UserInputError("Truncated or invalid Shapefile record")
        shape_type = struct.unpack_from("<i", data, content_start)[0]
        if shape_type != 0:
            if shape_type not in {5, 15, 25}:
                raise UserInputError("Shapefile contains a non-Polygon record")
            rings = _read_shapefile_rings(data[content_start:content_end], converter)
            polygons.extend(_rings_to_polygons(rings))
        offset = content_end
    if not polygons:
        raise UserInputError("Shapefile does not contain Polygon geometry")
    return RegionOfInterest(tuple(polygons))


def _collect_geojson_polygons(document: dict[str, Any], output: list[PolygonWGS84]) -> None:
    geometry_type = document.get("type")
    if geometry_type == "FeatureCollection":
        features = document.get("features")
        if not isinstance(features, list):
            raise UserInputError("GeoJSON FeatureCollection must contain a features array")
        for feature in features:
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise UserInputError("GeoJSON features array contains an invalid Feature")
            geometry = feature.get("geometry")
            if geometry is None:
                continue
            if not isinstance(geometry, dict):
                raise UserInputError("GeoJSON Feature geometry must be an object")
            _collect_geojson_polygons(geometry, output)
        return
    if geometry_type == "Feature":
        geometry = document.get("geometry")
        if not isinstance(geometry, dict):
            raise UserInputError("GeoJSON Feature must contain a geometry object")
        _collect_geojson_polygons(geometry, output)
        return
    if geometry_type == "GeometryCollection":
        geometries = document.get("geometries")
        if not isinstance(geometries, list):
            raise UserInputError("GeoJSON GeometryCollection must contain a geometries array")
        for geometry in geometries:
            if not isinstance(geometry, dict):
                raise UserInputError("GeoJSON GeometryCollection contains an invalid geometry")
            _collect_geojson_polygons(geometry, output)
        return
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        raise UserInputError(f"Unsupported GeoJSON geometry type: {geometry_type!r}")

    coordinates = document.get("coordinates")
    if not isinstance(coordinates, list):
        raise UserInputError(f"GeoJSON {geometry_type} must contain a coordinates array")
    raw_polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    for raw_polygon in raw_polygons:
        if not isinstance(raw_polygon, list) or not raw_polygon:
            raise UserInputError("GeoJSON polygon must contain an exterior ring")
        rings = []
        for raw_ring in raw_polygon:
            if not isinstance(raw_ring, list):
                raise UserInputError("GeoJSON polygon ring must be an array")
            rings.append(closed_ring(raw_ring))
        output.append(PolygonWGS84(exterior=rings[0], holes=tuple(rings[1:])))


def _parse_kml_coordinates(text: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for index, token in enumerate(text.split(), start=1):
        values = token.split(",")
        if len(values) < 2:
            raise UserInputError(f"KML coordinate {index} has no latitude")
        try:
            points.append((float(values[0]), float(values[1])))
        except ValueError as error:
            raise UserInputError(f"KML coordinate {index} is not numeric") from error
    return points


def _first_descendant_text(element: ElementTree.Element, name: str) -> str | None:
    for descendant in element.iter():
        if _local_name(descendant.tag) == name:
            return descendant.text
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _find_projection_file(source: Path) -> Path | None:
    for candidate in source.parent.iterdir():
        same_name = candidate.stem.lower() == source.stem.lower()
        if candidate.is_file() and same_name and candidate.suffix.lower() == ".prj":
            return candidate
    return None


def _shapefile_coordinate_converter(wkt: str) -> Callable[[float, float], tuple[float, float]]:
    normalized = wkt.upper()
    authority_codes = {
        int(value)
        for value in re.findall(
            r'(?:AUTHORITY|ID)\s*\[\s*["\']EPSG["\']\s*,\s*["\']?(\d+)',
            normalized,
        )
    }
    geographic = 4326 in authority_codes or 4258 in authority_codes
    if not authority_codes:
        geographic = "WGS_1984" in normalized or (
            "ETRS_1989" in normalized and "PROJCS" not in normalized
        )
    if geographic:
        return lambda x, y: (x, y)

    epsg_to_zone = {
        4083: 28,
        25828: 28,
        25829: 29,
        25830: 30,
        25831: 31,
        32628: 28,
        32629: 29,
        32630: 30,
        32631: 31,
    }
    selected = next((code for code in authority_codes if code in epsg_to_zone), None)
    if selected is None:
        match = re.search(r"UTM[^\d]*(?:ZONE[^\d]*)?(2[89]|3[01])\s*N?", normalized)
        if match:
            zone = int(match.group(1))
            selected = 25800 + zone
        else:
            raise UserInputError(
                "Unsupported Shapefile CRS; export as EPSG:4326, 4258, 4083, "
                "25828-25831, or 32628-32631"
            )
    zone = epsg_to_zone.get(selected, selected - 25800)
    crs = CRSInfo(selected, f"Imported UTM zone {zone}N", "Imported", zone)

    def convert(easting: float, northing: float) -> tuple[float, float]:
        point = project_utm_to_wgs84(ProjectedPoint(easting, northing, selected), crs)
        return point.longitude, point.latitude

    return convert


def _read_shapefile_rings(
    data: bytes, converter: Callable[[float, float], tuple[float, float]]
) -> list[tuple[tuple[float, float], ...]]:
    if len(data) < 44:
        raise UserInputError("Truncated Shapefile Polygon record")
    part_count, point_count = struct.unpack_from("<2i", data, 36)
    if part_count < 1 or point_count < 3 or part_count > point_count:
        raise UserInputError("Invalid Shapefile Polygon part or point count")
    points_offset = 44 + part_count * 4
    required_size = points_offset + point_count * 16
    if required_size > len(data):
        raise UserInputError("Truncated Shapefile Polygon coordinates")
    starts = list(struct.unpack_from(f"<{part_count}i", data, 44))
    if starts[0] != 0 or starts != sorted(set(starts)) or starts[-1] >= point_count:
        raise UserInputError("Invalid Shapefile Polygon part index")
    starts.append(point_count)
    points = [
        converter(*struct.unpack_from("<2d", data, points_offset + index * 16))
        for index in range(point_count)
    ]
    return [closed_ring(points[start:end]) for start, end in pairwise(starts)]


def _rings_to_polygons(
    rings: list[tuple[tuple[float, float], ...]],
) -> list[PolygonWGS84]:
    parents: list[int | None] = []
    for index, ring in enumerate(rings):
        containers = [
            candidate
            for candidate, other in enumerate(rings)
            if candidate != index and _point_in_raw_ring(ring[0], other)
        ]
        parents.append(
            min(
                containers,
                key=lambda candidate: abs(_raw_ring_area(rings[candidate])),
                default=None,
            )
        )

    depths = [_ring_depth(index, parents) for index in range(len(rings))]
    exterior_indexes = [index for index, depth in enumerate(depths) if depth % 2 == 0]
    result: list[PolygonWGS84] = []
    for exterior_index in exterior_indexes:
        holes = tuple(
            ring
            for index, ring in enumerate(rings)
            if depths[index] % 2 == 1 and parents[index] == exterior_index
        )
        result.append(PolygonWGS84(rings[exterior_index], holes))
    return result


def _ring_depth(index: int, parents: list[int | None]) -> int:
    depth = 0
    seen = {index}
    parent = parents[index]
    while parent is not None:
        if parent in seen:
            raise UserInputError("Shapefile Polygon rings have cyclic containment")
        seen.add(parent)
        depth += 1
        parent = parents[parent]
    return depth


def _raw_ring_area(ring: tuple[tuple[float, float], ...]) -> float:
    return sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in pairwise(ring)
    ) / 2.0


def _point_in_raw_ring(
    point: tuple[float, float], ring: tuple[tuple[float, float], ...]
) -> bool:
    x, y = point
    inside = False
    for left, right in pairwise(ring):
        if (left[1] > y) != (right[1] > y):
            crossing_x = (right[0] - left[0]) * (y - left[1]) / (right[1] - left[1]) + left[0]
            if x < crossing_x:
                inside = not inside
    return inside
