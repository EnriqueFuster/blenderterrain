from __future__ import annotations

import unittest

from blender_terrain.core import BBoxWGS84
from blender_terrain.errors import NoCoverageError
from blender_terrain.providers.spain_crs import (
    TerritoryGroup,
    classify_territory_envelope,
)
from blender_terrain.providers.spain_crs import (
    split_spain_bbox_by_utm_zone as split_bbox_by_utm_zone,
)


class TerritoryEnvelopeTests(unittest.TestCase):
    def test_classifies_supported_territory_groups(self) -> None:
        examples = (
            (BBoxWGS84(-3.8, 40.3, -3.6, 40.5), TerritoryGroup.ETRS89),
            (BBoxWGS84(2.5, 39.4, 2.8, 39.7), TerritoryGroup.ETRS89),
            (BBoxWGS84(-5.4, 35.8, -5.2, 35.95), TerritoryGroup.ETRS89),
            (BBoxWGS84(-16.7, 28.0, -16.4, 28.3), TerritoryGroup.CANARY_ISLANDS),
        )
        for bounds, expected in examples:
            with self.subTest(expected=expected):
                self.assertEqual(classify_territory_envelope(bounds), expected)

    def test_rejects_obviously_unsupported_locations(self) -> None:
        unsupported = (
            BBoxWGS84(2.2, 48.8, 2.5, 49.0),
            BBoxWGS84(-16.7, 39.0, -16.4, 39.3),
            BBoxWGS84(-25.0, 28.0, -24.0, 29.0),
        )
        for bounds in unsupported:
            with self.subTest(bounds=bounds), self.assertRaises(NoCoverageError):
                split_bbox_by_utm_zone(bounds)

    def test_rejects_roi_spanning_datum_families(self) -> None:
        with self.assertRaises(NoCoverageError):
            classify_territory_envelope(BBoxWGS84(-17.0, 28.0, -3.0, 40.0))

if __name__ == "__main__":
    unittest.main()
