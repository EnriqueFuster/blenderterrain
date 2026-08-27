from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import UserInputError
from blender_terrain.io.geopackage import (
    list_geopackage_polygon_layers,
    read_geopackage_roi,
)
from blender_terrain.io.roi_files import read_roi_file


def test_lists_only_polygon_feature_layers(tmp_path: Path) -> None:
    source = tmp_path / "areas.gpkg"
    _create_geopackage(source)

    layers = list_geopackage_polygon_layers(source)

    assert [(layer.name, layer.geometry_type, layer.srs_id) for layer in layers] == [
        ("study_area", "POLYGON", 4326)
    ]


def test_reads_selected_geopackage_polygon_layer(tmp_path: Path) -> None:
    source = tmp_path / "areas.gpkg"
    _create_geopackage(source)

    region = read_roi_file(source, "study_area")

    assert region.geometry_type == "Polygon"
    assert region.bounds == BBoxWGS84(-4, 40, -3, 41)


def test_requires_an_explicit_existing_layer(tmp_path: Path) -> None:
    source = tmp_path / "areas.gpkg"
    _create_geopackage(source)

    with pytest.raises(UserInputError, match="Choose a polygon layer"):
        read_roi_file(source)
    with pytest.raises(UserInputError, match="does not exist"):
        read_geopackage_roi(source, "missing")


def test_rejects_sqlite_database_without_geopackage_identifier(tmp_path: Path) -> None:
    source = tmp_path / "database.gpkg"
    sqlite3.connect(source).close()

    with pytest.raises(UserInputError, match="not marked as an OGC GeoPackage"):
        list_geopackage_polygon_layers(source)


def _create_geopackage(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id = 1196444487")
        connection.executescript(
            """
            CREATE TABLE gpkg_spatial_ref_sys (
                srs_name TEXT, srs_id INTEGER PRIMARY KEY, organization TEXT,
                organization_coordsys_id INTEGER, definition TEXT, description TEXT
            );
            CREATE TABLE gpkg_contents (
                table_name TEXT PRIMARY KEY, data_type TEXT, identifier TEXT,
                description TEXT, last_change TEXT, min_x REAL, min_y REAL,
                max_x REAL, max_y REAL, srs_id INTEGER
            );
            CREATE TABLE gpkg_geometry_columns (
                table_name TEXT, column_name TEXT, geometry_type_name TEXT,
                srs_id INTEGER, z INTEGER, m INTEGER
            );
            INSERT INTO gpkg_spatial_ref_sys VALUES
                ('WGS 84', 4326, 'EPSG', 4326, 'EPSG:4326', '');
            CREATE TABLE study_area (fid INTEGER PRIMARY KEY, geom BLOB);
            CREATE TABLE observation_points (fid INTEGER PRIMARY KEY, geom BLOB);
            INSERT INTO gpkg_contents (table_name, data_type, identifier, srs_id)
                VALUES ('study_area', 'features', 'study_area', 4326),
                       ('observation_points', 'features', 'observation_points', 4326);
            INSERT INTO gpkg_geometry_columns VALUES
                ('study_area', 'geom', 'POLYGON', 4326, 0, 0),
                ('observation_points', 'geom', 'POINT', 4326, 0, 0);
            """
        )
        connection.execute(
            "INSERT INTO study_area (geom) VALUES (?)",
            (_gpkg_polygon([(-4, 40), (-3, 40), (-3, 41), (-4, 41), (-4, 40)]),),
        )


def _gpkg_polygon(points: list[tuple[float, float]]) -> bytes:
    wkb = (
        struct.pack("<BI", 1, 3)
        + struct.pack("<I", 1)
        + struct.pack("<I", len(points))
        + b"".join(struct.pack("<2d", *point) for point in points)
    )
    return b"GP" + bytes((0, 1)) + struct.pack("<i", 4326) + wkb
