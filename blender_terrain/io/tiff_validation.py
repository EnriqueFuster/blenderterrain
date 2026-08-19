"""Minimal TIFF signature checks used before a source file reaches the cache."""

from __future__ import annotations

import struct
from pathlib import Path

from blender_terrain.errors import DownloadIntegrityError


def validate_tiff_header(path: Path) -> str:
    """Validate the structural TIFF header and its first directory offset."""

    file_size = path.stat().st_size
    with path.open("rb") as stream:
        header = stream.read(16)
    if len(header) < 8 or header[:2] not in {b"II", b"MM"}:
        raise DownloadIntegrityError("Downloaded resource has no valid TIFF byte order")

    byte_order = "<" if header[:2] == b"II" else ">"
    version = struct.unpack(f"{byte_order}H", header[2:4])[0]
    endian_name = "little-endian" if byte_order == "<" else "big-endian"
    if version == 42:
        first_ifd_offset = struct.unpack(f"{byte_order}I", header[4:8])[0]
        minimum_offset = 8
        description = f"classic {endian_name} TIFF"
    elif version == 43:
        if len(header) < 16:
            raise DownloadIntegrityError("Downloaded BigTIFF header is truncated")
        offset_size, reserved = struct.unpack(f"{byte_order}HH", header[4:8])
        if offset_size != 8 or reserved != 0:
            raise DownloadIntegrityError("Downloaded BigTIFF uses an unsupported header layout")
        first_ifd_offset = struct.unpack(f"{byte_order}Q", header[8:16])[0]
        minimum_offset = 16
        description = f"{endian_name} BigTIFF"
    else:
        raise DownloadIntegrityError("Downloaded resource has an unsupported TIFF version")

    if not minimum_offset <= first_ifd_offset < file_size:
        raise DownloadIntegrityError("Downloaded TIFF has an invalid first directory offset")
    return description
