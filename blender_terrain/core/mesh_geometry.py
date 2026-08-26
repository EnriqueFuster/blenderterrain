"""Portable conversion from elevation nodes to Blender-ready mesh buffers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds


@dataclass(frozen=True, slots=True)
class TerrainMeshGeometry:
    """Local XYZ vertices and counter-clockwise quad indices."""

    vertices: NDArray[np.float32]
    faces: NDArray[np.int32]


def build_terrain_mesh_geometry(
    elevation: NDArray[np.float32], bounds: ProjectedBounds, nodata: float
) -> TerrainMeshGeometry:
    """Build a north-up quad grid, omitting faces that touch NoData nodes."""

    if elevation.dtype != np.float32 or elevation.ndim != 2:
        raise RasterFormatError("Terrain elevation must be a two-dimensional Float32 grid")
    rows, columns = elevation.shape
    if rows < 2 or columns < 2:
        raise RasterFormatError("Terrain elevation requires at least two rows and columns")
    x = np.linspace(0.0, bounds.east - bounds.west, columns, dtype=np.float32)
    y = np.linspace(bounds.north - bounds.south, 0.0, rows, dtype=np.float32)
    xx, yy = np.meshgrid(x, y)
    valid = elevation != nodata
    vertices = np.column_stack(
        (xx.ravel(), yy.ravel(), np.where(valid, elevation, 0.0).ravel())
    ).astype(np.float32, copy=False)

    top_left = np.arange((rows - 1) * (columns - 1), dtype=np.int32).reshape(
        rows - 1, columns - 1
    )
    top_left += np.arange(rows - 1, dtype=np.int32)[:, None]
    bottom_left = top_left + columns
    faces = np.stack(
        (top_left, bottom_left, bottom_left + 1, top_left + 1), axis=-1
    )
    valid_faces = (
        valid[:-1, :-1]
        & valid[1:, :-1]
        & valid[1:, 1:]
        & valid[:-1, 1:]
    )
    return TerrainMeshGeometry(vertices, faces[valid_faces].reshape(-1, 4))
