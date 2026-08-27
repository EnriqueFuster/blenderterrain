from __future__ import annotations

import pytest

from blender_terrain.core.roi import (
    BBoxWGS84,
    PolygonWGS84,
    RegionOfInterest,
    closed_ring,
)
from blender_terrain.errors import UserInputError


def test_region_from_bbox_preserves_bounds_and_serializes_polygon() -> None:
    bounds = BBoxWGS84(west=-3.8, south=40.3, east=-3.6, north=40.5)

    region = RegionOfInterest.from_bbox(bounds)

    assert region.bounds == bounds
    assert region.geometry_type == "Polygon"
    assert region.to_geojson_geometry() == {
        "type": "Polygon",
        "coordinates": [[
            [-3.8, 40.3],
            [-3.6, 40.3],
            [-3.6, 40.5],
            [-3.8, 40.5],
            [-3.8, 40.3],
        ]],
    }


def test_closed_ring_accepts_altitude_and_closes_open_input() -> None:
    ring = closed_ring(((1, 2, 100), (3, 2, 200), (3, 4, 300), (1, 4, 400)))

    assert ring == ((1.0, 2.0), (3.0, 2.0), (3.0, 4.0), (1.0, 4.0), (1.0, 2.0))
    assert PolygonWGS84(ring).vertex_count == 5


def test_polygon_accepts_a_hole_inside_its_exterior() -> None:
    polygon = PolygonWGS84(
        exterior=closed_ring(((0, 0), (4, 0), (4, 4), (0, 4))),
        holes=(closed_ring(((1, 1), (2, 1), (2, 2), (1, 2))),),
    )

    assert polygon.vertex_count == 10


@pytest.mark.parametrize(
    ("ring", "message"),
    [
        (((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)), "closed"),
        (closed_ring(((0, 0), (1, 0), (2, 0))), "zero area"),
        (closed_ring(((0, 0), (2, 2), (0, 2), (2, 0))), "intersects itself"),
        (closed_ring(((0, 0), (181, 0), (181, 1))), "outside WGS84"),
    ],
)
def test_polygon_rejects_invalid_exterior(ring: object, message: str) -> None:
    with pytest.raises(UserInputError, match=message):
        PolygonWGS84(ring)  # type: ignore[arg-type]


def test_polygon_rejects_hole_outside_exterior() -> None:
    with pytest.raises(UserInputError, match="lies outside"):
        PolygonWGS84(
            exterior=closed_ring(((0, 0), (2, 0), (2, 2), (0, 2))),
            holes=(closed_ring(((3, 3), (4, 3), (4, 4), (3, 4))),),
        )


def test_region_rejects_overlapping_polygon_parts() -> None:
    with pytest.raises(UserInputError, match="parts 1 and 2 overlap"):
        RegionOfInterest(
            (
                PolygonWGS84(closed_ring(((0, 0), (2, 0), (2, 2), (0, 2)))),
                PolygonWGS84(closed_ring(((1, 1), (3, 1), (3, 3), (1, 3)))),
            )
        )


def test_region_serializes_disjoint_parts_as_multipolygon() -> None:
    region = RegionOfInterest(
        (
            PolygonWGS84(closed_ring(((0, 0), (1, 0), (1, 1), (0, 1)))),
            PolygonWGS84(closed_ring(((2, 2), (3, 2), (3, 3), (2, 3)))),
        )
    )

    assert region.geometry_type == "MultiPolygon"
    assert region.bounds == BBoxWGS84(west=0, south=0, east=3, north=3)
    assert region.to_geojson_geometry()["type"] == "MultiPolygon"
