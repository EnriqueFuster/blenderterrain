from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from blender_terrain.io.elevation_output import write_elevation_array


class ElevationOutputTests(unittest.TestCase):
    def test_writes_loadable_float32_array_atomically(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "tile.npy"
            expected = np.arange(6, dtype=np.float32).reshape(2, 3)

            write_elevation_array(path, expected)

            np.testing.assert_array_equal(np.load(path, allow_pickle=False), expected)
            self.assertFalse(path.with_name("tile.npy.part").exists())


if __name__ == "__main__":
    unittest.main()
