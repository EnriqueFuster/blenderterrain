from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from blender_terrain.core import inspect_local_imagery
from blender_terrain.errors import UserInputError
from blender_terrain.models import ProjectedBounds


def test_inspects_georeferenced_local_png(tmp_path: Path) -> None:
    path = tmp_path / "ortho.png"
    path.write_bytes(_png_file(2, 3))
    path.with_suffix(".pgw").write_text(
        "1\n0\n0\n-1\n100.5\n200.5\n", encoding="utf-8"
    )
    path.with_suffix(".prj").write_text(
        'PROJCS["ETRS89 / UTM zone 30N",AUTHORITY["EPSG","25830"]]',
        encoding="utf-8",
    )

    inspection = inspect_local_imagery(path)

    assert inspection.width == 2
    assert inspection.height == 3
    assert inspection.gsd_metres == 1.0
    assert inspection.bounds == ProjectedBounds(100.0, 198.0, 102.0, 201.0, 25830)


def test_requires_world_and_projection_sidecars(tmp_path: Path) -> None:
    path = tmp_path / "ortho.png"
    path.write_bytes(_png_file(2, 2))

    with pytest.raises(UserInputError, match=r"pgw or \.wld"):
        inspect_local_imagery(path)


def test_rejects_rotated_world_file(tmp_path: Path) -> None:
    path = tmp_path / "ortho.png"
    path.write_bytes(_png_file(2, 2))
    path.with_suffix(".pgw").write_text(
        "1\n0.1\n0\n-1\n100.5\n200.5\n", encoding="utf-8"
    )
    path.with_suffix(".prj").write_text(
        'PROJCS["ETRS89 / UTM zone 30N",AUTHORITY["EPSG","25830"]]',
        encoding="utf-8",
    )

    with pytest.raises(UserInputError, match="Rotated"):
        inspect_local_imagery(path)


def _png_file(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height)))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(data, zlib.crc32(kind))
    return struct.pack(">I4s", len(data), kind) + data + struct.pack(">I", checksum)
