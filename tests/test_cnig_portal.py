"""Offline tests for CNIG discovery request construction."""

from __future__ import annotations

import json
import unittest

from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.providers.cnig_portal import _bbox_feature_collection


class BBoxWGS84Tests(unittest.TestCase):
    def test_serializes_closed_counterclockwise_polygon(self) -> None:
        bbox = BBoxWGS84(west=-0.39, south=39.46, east=-0.37, north=39.48)

        payload = json.loads(_bbox_feature_collection(bbox))
        ring = payload["features"][0]["geometry"]["coordinates"][0]

        self.assertEqual(ring[0], [-0.39, 39.46])
        self.assertEqual(ring[-1], ring[0])
        self.assertEqual(len(ring), 5)


if __name__ == "__main__":
    unittest.main()
