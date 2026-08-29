"""Atomic persistence for processed Float32 elevation arrays."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import RasterFormatError
from .atomic import finalize_part


def write_elevation_array(path: Path, data: NDArray[np.float32]) -> None:
    """Write one two-dimensional Float32 NumPy array without partial publication."""

    if path.suffix.lower() != ".npy":
        raise RasterFormatError("Processed elevation output must use a .npy extension")
    if data.dtype != np.float32 or data.ndim != 2 or not data.size:
        raise RasterFormatError("Processed elevation output must be a non-empty Float32 grid")
    _write_array(path, data)


def write_quality_array(path: Path, data: NDArray[np.uint8]) -> None:
    """Write one two-dimensional UInt8 quality grid atomically."""

    if path.suffix.lower() != ".npy":
        raise RasterFormatError("Processed quality output must use a .npy extension")
    if data.dtype != np.uint8 or data.ndim != 2 or not data.size:
        raise RasterFormatError("Processed quality output must be a non-empty UInt8 grid")
    _write_array(path, data)


def _write_array(path: Path, data: NDArray[np.float32] | NDArray[np.uint8]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise RasterFormatError(f"Processed elevation output already exists: {path.name}")
    part_path = path.with_name(path.name + ".part")
    try:
        with part_path.open("xb") as stream:
            np.save(stream, data, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(part_path, path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise
