"""Minimal TIFF signature checks used before a source file reaches the cache."""

from __future__ import annotations

from pathlib import Path

from blender_terrain.errors import DownloadIntegrityError


TIFF_SIGNATURES = {
    b"II*\x00": "classic little-endian TIFF",
    b"MM\x00*": "classic big-endian TIFF",
    b"II+\x00": "little-endian BigTIFF",
    b"MM\x00+": "big-endian BigTIFF",
}


def validate_tiff_signature(path: Path) -> str:
    """Validate a TIFF or BigTIFF byte-order and version signature."""

    with path.open("rb") as stream:
        signature = stream.read(4)
    try:
        return TIFF_SIGNATURES[signature]
    except KeyError as exc:
        raise DownloadIntegrityError(
            "Downloaded resource does not start with a TIFF signature"
        ) from exc
