"""Aligned projected grids and deterministic output tiling."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import RasterAlignmentError
from ..models import ProjectedBounds

_GRID_TOLERANCE = 1e-8
DEFAULT_MAX_TILE_CELLS = 512


@dataclass(frozen=True, slots=True)
class GridSpec:
    """A north-up pixel-is-area grid with a top-left row origin."""

    bounds: ProjectedBounds
    resolution: float
    columns: int
    rows: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.resolution) or self.resolution <= 0.0:
            raise RasterAlignmentError("Grid resolution must be a positive finite value")
        if self.columns <= 0 or self.rows <= 0:
            raise RasterAlignmentError("Grid dimensions must be positive")
        expected_width = self.columns * self.resolution
        expected_height = self.rows * self.resolution
        if not math.isclose(
            self.bounds.east - self.bounds.west,
            expected_width,
            rel_tol=_GRID_TOLERANCE,
            abs_tol=_GRID_TOLERANCE,
        ) or not math.isclose(
            self.bounds.north - self.bounds.south,
            expected_height,
            rel_tol=_GRID_TOLERANCE,
            abs_tol=_GRID_TOLERANCE,
        ):
            raise RasterAlignmentError("Grid dimensions do not match bounds and resolution")

    @property
    def sample_count(self) -> int:
        """Return the number of raster cells in the grid."""

        return self.columns * self.rows


@dataclass(frozen=True, slots=True)
class GridTile:
    """A non-overlapping cell window within one GridSpec."""

    row: int
    column: int
    row_offset: int
    column_offset: int
    rows: int
    columns: int
    bounds: ProjectedBounds

    @property
    def sample_count(self) -> int:
        """Return the number of raster cells in this tile."""

        return self.columns * self.rows


def align_projected_grid(bounds: ProjectedBounds, resolution: float) -> GridSpec:
    """Expand projected bounds outwards to a global resolution-aligned grid."""

    if not math.isfinite(resolution) or resolution <= 0.0:
        raise RasterAlignmentError("Grid resolution must be a positive finite value")
    west_index = math.floor(bounds.west / resolution)
    south_index = math.floor(bounds.south / resolution)
    east_index = math.ceil(bounds.east / resolution)
    north_index = math.ceil(bounds.north / resolution)
    columns = east_index - west_index
    rows = north_index - south_index
    aligned = ProjectedBounds(
        west=west_index * resolution,
        south=south_index * resolution,
        east=east_index * resolution,
        north=north_index * resolution,
        epsg=bounds.epsg,
    )
    return GridSpec(aligned, resolution, columns, rows)


def tile_grid(
    grid: GridSpec, maximum_tile_cells: int = DEFAULT_MAX_TILE_CELLS
) -> tuple[GridTile, ...]:
    """Split a grid north-to-south and west-to-east into bounded cell windows."""

    if maximum_tile_cells <= 0:
        raise RasterAlignmentError("Maximum tile cells must be positive")
    tile_rows = math.ceil(grid.rows / maximum_tile_cells)
    tile_columns = math.ceil(grid.columns / maximum_tile_cells)
    tiles: list[GridTile] = []
    for tile_row in range(tile_rows):
        row_offset = tile_row * maximum_tile_cells
        rows = min(maximum_tile_cells, grid.rows - row_offset)
        north = grid.bounds.north - row_offset * grid.resolution
        south = north - rows * grid.resolution
        for tile_column in range(tile_columns):
            column_offset = tile_column * maximum_tile_cells
            columns = min(maximum_tile_cells, grid.columns - column_offset)
            west = grid.bounds.west + column_offset * grid.resolution
            east = west + columns * grid.resolution
            tiles.append(
                GridTile(
                    row=tile_row,
                    column=tile_column,
                    row_offset=row_offset,
                    column_offset=column_offset,
                    rows=rows,
                    columns=columns,
                    bounds=ProjectedBounds(west, south, east, north, grid.bounds.epsg),
                )
            )
    return tuple(tiles)


def tile_grid_manual(
    grid: GridSpec,
    tile_rows: int,
    tile_columns: int,
    maximum_tile_cells: int = DEFAULT_MAX_TILE_CELLS,
) -> tuple[GridTile, ...]:
    """Split a grid into an exact balanced row/column layout."""

    if tile_rows <= 0 or tile_columns <= 0:
        raise RasterAlignmentError("Manual tile rows and columns must be positive")
    if tile_rows > grid.rows or tile_columns > grid.columns:
        raise RasterAlignmentError("Manual tile layout exceeds the available grid cells")
    row_sizes = _partition_cells(grid.rows, tile_rows)
    column_sizes = _partition_cells(grid.columns, tile_columns)
    if max(row_sizes) > maximum_tile_cells or max(column_sizes) > maximum_tile_cells:
        raise RasterAlignmentError(
            "Manual terrain tiles exceed the safe 512 by 512 cell limit; use more rows or columns"
        )
    tiles: list[GridTile] = []
    row_offset = 0
    for tile_row, rows in enumerate(row_sizes):
        north = grid.bounds.north - row_offset * grid.resolution
        south = north - rows * grid.resolution
        column_offset = 0
        for tile_column, columns in enumerate(column_sizes):
            west = grid.bounds.west + column_offset * grid.resolution
            east = west + columns * grid.resolution
            tiles.append(
                GridTile(
                    tile_row,
                    tile_column,
                    row_offset,
                    column_offset,
                    rows,
                    columns,
                    ProjectedBounds(west, south, east, north, grid.bounds.epsg),
                )
            )
            column_offset += columns
        row_offset += rows
    return tuple(tiles)


def _partition_cells(cell_count: int, part_count: int) -> tuple[int, ...]:
    size, remainder = divmod(cell_count, part_count)
    return tuple(size + (1 if index < remainder else 0) for index in range(part_count))
