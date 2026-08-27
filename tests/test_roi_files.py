from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from blender_terrain.errors import UserInputError
from blender_terrain.io.roi_files import read_geojson_roi, read_kml_roi, read_roi_file


def test_geojson_feature_collection_combines_polygon_parts() -> None:
    document = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "mainland"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-4, 40], [-3, 40], [-3, 41], [-4, 41], [-4, 40]]],
                },
            },
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1, 38], [2, 38], [2, 39], [1, 39], [1, 38]]],
                },
            },
        ],
    }

    region = read_geojson_roi(json.dumps(document))

    assert region.geometry_type == "MultiPolygon"
    assert len(region.polygons) == 2
    assert region.bounds.west == -4
    assert region.bounds.east == 2


def test_geojson_reads_polygon_hole_and_optional_altitude() -> None:
    document = {
        "type": "Polygon",
        "coordinates": [
            [[-4, 40, 5], [-2, 40, 6], [-2, 42, 7], [-4, 42, 8], [-4, 40, 5]],
            [[-3.5, 40.5], [-3, 40.5], [-3, 41], [-3.5, 41], [-3.5, 40.5]],
        ],
    }

    region = read_geojson_roi(json.dumps(document))

    assert len(region.polygons[0].holes) == 1
    assert region.polygons[0].exterior[0] == (-4.0, 40.0)


def test_geojson_rejects_custom_crs_and_non_polygon_geometry() -> None:
    with pytest.raises(UserInputError, match="EPSG:4326"):
        read_geojson_roi(json.dumps({"type": "Polygon", "coordinates": [], "crs": {}}))
    with pytest.raises(UserInputError, match="Unsupported GeoJSON geometry type"):
        read_geojson_roi(json.dumps({"type": "Point", "coordinates": [-3, 40]}))


def test_kml_reads_multiple_polygons_and_a_hole() -> None:
    text = """<?xml version="1.0" encoding="UTF-8"?>
    <kml xmlns="http://www.opengis.net/kml/2.2"><Document>
      <Placemark><Polygon>
        <outerBoundaryIs><LinearRing><coordinates>
          -4,40,0 -2,40,0 -2,42,0 -4,42,0 -4,40,0
        </coordinates></LinearRing></outerBoundaryIs>
        <innerBoundaryIs><LinearRing><coordinates>
          -3.5,40.5 -3,40.5 -3,41 -3.5,41 -3.5,40.5
        </coordinates></LinearRing></innerBoundaryIs>
      </Polygon></Placemark>
      <Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>
        1,38 2,38 2,39 1,39 1,38
      </coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>
    </Document></kml>"""

    region = read_kml_roi(text)

    assert region.geometry_type == "MultiPolygon"
    assert len(region.polygons) == 2
    assert len(region.polygons[0].holes) == 1


def test_kml_rejects_xml_entities() -> None:
    with pytest.raises(UserInputError, match="entity declarations"):
        read_kml_roi("<!DOCTYPE x [<!ENTITY y 'z'>]><kml/>")


def test_read_roi_file_dispatches_geojson(tmp_path: Path) -> None:
    source = tmp_path / "area.geojson"
    source.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [[[-4, 40], [-3, 40], [-3, 41], [-4, 41], [-4, 40]]],
            }
        ),
        encoding="utf-8",
    )

    assert read_roi_file(source).geometry_type == "Polygon"


def test_read_roi_file_reads_wgs84_polygon_shapefile(tmp_path: Path) -> None:
    source = tmp_path / "area.shp"
    points = [(-4.0, 40.0), (-4.0, 41.0), (-3.0, 41.0), (-3.0, 40.0), (-4.0, 40.0)]
    source.write_bytes(_polygon_shapefile(points))
    source.with_suffix(".prj").write_text(
        'GEOGCS["WGS 84",DATUM["WGS_1984"],AUTHORITY["EPSG","4326"]]',
        encoding="utf-8",
    )

    region = read_roi_file(source)

    assert region.geometry_type == "Polygon"
    assert region.bounds.west == -4
    assert region.bounds.north == 41


def test_shapefile_requires_projection_file(tmp_path: Path) -> None:
    source = tmp_path / "area.shp"
    source.write_bytes(_polygon_shapefile([(-4, 40), (-4, 41), (-3, 41), (-4, 40)]))

    with pytest.raises(UserInputError, match=r"accompanying \.prj"):
        read_roi_file(source)


def _polygon_shapefile(points: list[tuple[float, float]]) -> bytes:
    west = min(point[0] for point in points)
    south = min(point[1] for point in points)
    east = max(point[0] for point in points)
    north = max(point[1] for point in points)
    content = (
        struct.pack("<i4d2i", 5, west, south, east, north, 1, len(points))
        + struct.pack("<i", 0)
        + b"".join(struct.pack("<2d", *point) for point in points)
    )
    file_size = 100 + 8 + len(content)
    header = (
        struct.pack(">7i", 9994, 0, 0, 0, 0, 0, file_size // 2)
        + struct.pack("<2i8d", 1000, 5, west, south, east, north, 0, 0, 0, 0)
    )
    return header + struct.pack(">2i", 1, len(content) // 2) + content
