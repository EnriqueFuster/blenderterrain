"""Portable conversion from elevation nodes to Blender-ready mesh buffers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from ..models import ProjectedBounds

PREVIEW_MESH_REDUCTION_FACTOR = 16
DEFAULT_PREVIEW_SUBDIVISION_LEVEL = 1


def native_resolution_subdivision_level(reduction_factor: int) -> int:
    """Return the first subdivision level that reaches the source sample spacing."""

    if isinstance(reduction_factor, bool) or not isinstance(reduction_factor, int):
        raise ValueError("Mesh reduction factor must be an integer")
    if reduction_factor < 1:
        raise ValueError("Mesh reduction factor must be positive")
    return math.ceil(math.log2(reduction_factor))


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


def build_displacement_mesh_geometry(
    elevation: NDArray[np.float32],
    bounds: ProjectedBounds,
    nodata: float,
    baseline: float,
    reduction_factor: int = 1,
) -> TerrainMeshGeometry:
    """Build a flat native or reduced grid for a full-resolution heightmap."""

    if not math.isfinite(baseline):
        raise RasterFormatError("Terrain displacement baseline must be finite")
    if isinstance(reduction_factor, bool) or not isinstance(reduction_factor, int):
        raise RasterFormatError("Terrain mesh reduction factor must be an integer")
    if reduction_factor < 1:
        raise RasterFormatError("Terrain mesh reduction factor must be positive")
    reduced = elevation[
        np.ix_(
            _reduced_axis_indices(elevation.shape[0], reduction_factor),
            _reduced_axis_indices(elevation.shape[1], reduction_factor),
        )
    ]
    baked = build_terrain_mesh_geometry(reduced, bounds, nodata)
    vertices = baked.vertices.copy()
    vertices[:, 2] = baseline
    return TerrainMeshGeometry(vertices, baked.faces)


def _reduced_axis_indices(length: int, factor: int) -> NDArray[np.intp]:
    if length < 2:
        return np.arange(length, dtype=np.intp)
    native_cells = length - 1
    reduced_cells = max(1, math.ceil(native_cells / factor))
    return cast(
        NDArray[np.intp],
        np.rint(np.linspace(0, native_cells, reduced_cells + 1)).astype(np.intp),
    )
