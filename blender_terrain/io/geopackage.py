"""Read polygon layers from an OGC GeoPackage using Python's SQLite support."""

from __future__ import annotations

import sqlite3
import struct
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..core.crs import CRSInfo
from ..core.projection import ProjectedPoint, project_utm_to_wgs84
from ..core.roi import PolygonWGS84, RegionOfInterest, closed_ring
from ..errors import UserInputError


@dataclass(frozen=True, slots=True)
class GeoPackageLayer:
    """Metadata needed to present one selectable polygon feature table."""

    name: str
    geometry_column: str
    geometry_type: str
    srs_id: int


def list_geopackage_polygon_layers(path: str | Path) -> tuple[GeoPackageLayer, ...]:
    """List Polygon and MultiPolygon feature tables without reading their features."""

    source = Path(path).resolve()
    try:
        with closing(_open_read_only(source)) as connection:
            rows = connection.execute(
                """
                SELECT c.table_name, g.column_name, UPPER(g.geometry_type_name), g.srs_id
                FROM gpkg_contents AS c
                JOIN gpkg_geometry_columns AS g ON g.table_name = c.table_name
                WHERE c.data_type = 'features'
                  AND UPPER(g.geometry_type_name) IN ('POLYGON', 'MULTIPOLYGON')
                ORDER BY c.table_name
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise UserInputError(f"Cannot inspect GeoPackage metadata: {error}") from error
    return tuple(
        GeoPackageLayer(str(row[0]), str(row[1]), str(row[2]), int(row[3]))
        for row in rows
    )


def read_geopackage_roi(path: str | Path, layer_name: str) -> RegionOfInterest:
    """Read every non-empty Polygon or MultiPolygon feature from one layer."""

    source = Path(path).resolve()
    layers = list_geopackage_polygon_layers(source)
    layer = next((candidate for candidate in layers if candidate.name == layer_name), None)
    if layer is None:
        raise UserInputError(f"GeoPackage polygon layer does not exist: {layer_name!r}")
    try:
        with closing(_open_read_only(source)) as connection:
            converter = _geopackage_converter(connection, layer.srs_id)
            identifier = _quoted_identifier(layer.geometry_column)
            table = _quoted_identifier(layer.name)
            geometries = connection.execute(
                f"SELECT {identifier} FROM {table} WHERE {identifier} IS NOT NULL"
            )
            polygons: list[PolygonWGS84] = []
            vertex_count = 0
            for (blob,) in geometries:
                if not isinstance(blob, bytes):
                    raise UserInputError("GeoPackage geometry value is not binary")
                feature_polygons = _read_geopackage_geometry(blob, layer.srs_id, converter)
                vertex_count += sum(polygon.vertex_count for polygon in feature_polygons)
                if vertex_count > 1_000_000:
                    raise UserInputError(
                        "The GeoPackage ROI exceeds the one-million-vertex limit"
                    )
                polygons.extend(feature_polygons)
    except sqlite3.Error as error:
        raise UserInputError(f"Cannot read GeoPackage layer {layer.name!r}: {error}") from error
    if not polygons:
        raise UserInputError(f"GeoPackage layer {layer.name!r} contains no polygon geometry")
    return RegionOfInterest(tuple(polygons))


def _open_read_only(source: Path) -> sqlite3.Connection:
    if not source.is_file():
        raise UserInputError(f"GeoPackage does not exist: {source}")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
    except sqlite3.Error as error:
        if connection is not None:
            connection.close()
        raise UserInputError(f"Cannot open GeoPackage: {error}") from error
    if application_id != 0x47504B47:
        connection.close()
        raise UserInputError("SQLite file is not marked as an OGC GeoPackage")
    return connection


def _geopackage_converter(
    connection: sqlite3.Connection, srs_id: int
) -> Callable[[float, float], tuple[float, float]]:
    row = connection.execute(
        """
        SELECT organization, organization_coordsys_id, definition
        FROM gpkg_spatial_ref_sys WHERE srs_id = ?
        """,
        (srs_id,),
    ).fetchone()
    if row is None:
        raise UserInputError(f"GeoPackage CRS definition is missing for SRS {srs_id}")
    organization, organization_code, _definition = row
    epsg = int(organization_code) if str(organization).upper() == "EPSG" else None
    if epsg in {4326, 4258}:
        return lambda x, y: (x, y)
    zones = {
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
    if epsg not in zones:
        raise UserInputError(
            f"Unsupported GeoPackage CRS {organization}:{organization_code}; "
            "export as EPSG:4326, 4258, 4083, 25828-25831, or 32628-32631"
        )
    crs = CRSInfo(epsg, f"Imported UTM zone {zones[epsg]}N", "Imported", zones[epsg])

    def convert(easting: float, northing: float) -> tuple[float, float]:
        point = project_utm_to_wgs84(ProjectedPoint(easting, northing, epsg), crs)
        return point.longitude, point.latitude

    return convert


def _read_geopackage_geometry(
    blob: bytes,
    expected_srs_id: int,
    converter: Callable[[float, float], tuple[float, float]],
) -> list[PolygonWGS84]:
    if len(blob) < 8 or blob[:2] != b"GP":
        raise UserInputError("Invalid GeoPackage geometry header")
    flags = blob[3]
    endian = "<" if flags & 1 else ">"
    envelope_indicator = (flags >> 1) & 0b111
    envelope_sizes = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
    if envelope_indicator not in envelope_sizes:
        raise UserInputError("Unsupported GeoPackage geometry envelope")
    srs_id = struct.unpack_from(f"{endian}i", blob, 4)[0]
    if srs_id != expected_srs_id:
        raise UserInputError("GeoPackage feature CRS does not match its layer metadata")
    if flags & 0b00010000:
        return []
    offset = 8 + envelope_sizes[envelope_indicator] * 8
    polygons, final_offset = _read_wkb(blob, offset, converter)
    if final_offset != len(blob):
        raise UserInputError("GeoPackage geometry contains unexpected trailing data")
    return polygons


def _read_wkb(
    data: bytes,
    offset: int,
    converter: Callable[[float, float], tuple[float, float]],
) -> tuple[list[PolygonWGS84], int]:
    if offset + 5 > len(data):
        raise UserInputError("Truncated WKB geometry")
    byte_order = data[offset]
    if byte_order not in {0, 1}:
        raise UserInputError("Invalid WKB byte order")
    endian = "<" if byte_order == 1 else ">"
    geometry_type = struct.unpack_from(f"{endian}I", data, offset + 1)[0]
    offset += 5
    has_z = bool(geometry_type & 0x80000000)
    has_m = bool(geometry_type & 0x40000000)
    has_srid = bool(geometry_type & 0x20000000)
    base_type = geometry_type & 0x000000FF
    dimensional_type = geometry_type & 0x0FFFFFFF
    if 1000 <= dimensional_type < 4000:
        dimensions, base_type = divmod(dimensional_type, 1000)
        has_z = dimensions in {1, 3}
        has_m = dimensions in {2, 3}
    if has_srid:
        if offset + 4 > len(data):
            raise UserInputError("Truncated EWKB SRID")
        offset += 4
    if base_type == 3:
        polygon, offset = _read_wkb_polygon(data, offset, endian, has_z, has_m, converter)
        return [polygon], offset
    if base_type != 6:
        raise UserInputError("GeoPackage layer contains a non-polygon WKB geometry")
    count, offset = _read_uint32(data, offset, endian)
    polygons: list[PolygonWGS84] = []
    for _ in range(count):
        child, offset = _read_wkb(data, offset, converter)
        if len(child) != 1:
            raise UserInputError("Invalid MultiPolygon child geometry")
        polygons.extend(child)
    return polygons, offset


def _read_wkb_polygon(
    data: bytes,
    offset: int,
    endian: str,
    has_z: bool,
    has_m: bool,
    converter: Callable[[float, float], tuple[float, float]],
) -> tuple[PolygonWGS84, int]:
    ring_count, offset = _read_uint32(data, offset, endian)
    if ring_count == 0:
        raise UserInputError("GeoPackage Polygon has no exterior ring")
    coordinate_size = 2 + int(has_z) + int(has_m)
    rings = []
    for _ in range(ring_count):
        point_count, offset = _read_uint32(data, offset, endian)
        byte_count = point_count * coordinate_size * 8
        if point_count < 4 or offset + byte_count > len(data):
            raise UserInputError("Invalid or truncated GeoPackage Polygon ring")
        points = []
        for point_index in range(point_count):
            values = struct.unpack_from(
                f"{endian}{coordinate_size}d",
                data,
                offset + point_index * coordinate_size * 8,
            )
            points.append(converter(values[0], values[1]))
        offset += byte_count
        rings.append(closed_ring(points))
    return PolygonWGS84(rings[0], tuple(rings[1:])), offset


def _read_uint32(data: bytes, offset: int, endian: str) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise UserInputError("Truncated WKB geometry")
    return struct.unpack_from(f"{endian}I", data, offset)[0], offset + 4


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
