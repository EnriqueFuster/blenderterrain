from __future__ import annotations

import unittest

from blender_terrain.core import GridSpec, align_projected_grid, tile_grid
from blender_terrain.errors import RasterAlignmentError
from blender_terrain.models import ProjectedBounds


class GridSpecTests(unittest.TestCase):
    def test_expands_unaligned_bounds_to_outer_resolution_edges(self) -> None:
        requested = ProjectedBounds(724_001.2, 4_372_002.1, 724_999.1, 4_373_000.2, 25830)

        grid = align_projected_grid(requested, 5.0)

        self.assertEqual(
            grid.bounds,
            ProjectedBounds(724_000.0, 4_372_000.0, 725_000.0, 4_373_005.0, 25830),
        )
        self.assertEqual((grid.columns, grid.rows), (200, 201))
        self.assertEqual(grid.sample_count, 40_200)

    def test_rejects_dimensions_inconsistent_with_bounds(self) -> None:
        with self.assertRaises(RasterAlignmentError):
            GridSpec(
                ProjectedBounds(0.0, 0.0, 100.0, 100.0, 25830),
                resolution=2.0,
                columns=49,
                rows=50,
            )


class GridTilingTests(unittest.TestCase):
    def test_splits_in_stable_row_major_order(self) -> None:
        grid = align_projected_grid(
            ProjectedBounds(0.0, 0.0, 1_000.0, 1_000.0, 25830), 2.0
        )

        tiles = tile_grid(grid, maximum_tile_cells=256)

        self.assertEqual(
            [(tile.row, tile.column) for tile in tiles],
            [(0, 0), (0, 1), (1, 0), (1, 1)],
        )
        self.assertEqual((tiles[0].rows, tiles[0].columns), (256, 256))
        self.assertEqual((tiles[-1].rows, tiles[-1].columns), (244, 244))
        self.assertEqual(sum(tile.sample_count for tile in tiles), grid.sample_count)

    def test_adjacent_tiles_share_exact_projected_edges(self) -> None:
        grid = align_projected_grid(
            ProjectedBounds(724_000.0, 4_372_000.0, 725_000.0, 4_373_000.0, 25830),
            2.0,
        )
        northwest, northeast, southwest, _southeast = tile_grid(grid, 256)

        self.assertEqual(northwest.bounds.east, northeast.bounds.west)
        self.assertEqual(northwest.bounds.south, southwest.bounds.north)
        self.assertEqual(northwest.bounds.north, northeast.bounds.north)

    def test_rejects_non_positive_tile_size(self) -> None:
        grid = align_projected_grid(ProjectedBounds(0.0, 0.0, 10.0, 10.0, 25830), 2.0)

        with self.assertRaises(RasterAlignmentError):
            tile_grid(grid, 0)


if __name__ == "__main__":
    unittest.main()
