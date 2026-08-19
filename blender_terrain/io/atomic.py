"""Safe, bounded file writing for downloaded source resources."""

from __future__ import annotations

import os
from pathlib import Path

from blender_terrain.errors import DownloadIntegrityError


def safe_destination(cache_directory: Path, server_filename: str) -> Path:
    """Return a destination inside cache_directory or reject unsafe names."""

    if not server_filename or Path(server_filename).name != server_filename:
        raise DownloadIntegrityError("Server filename is empty or contains a path component")
    if server_filename in {".", ".."}:
        raise DownloadIntegrityError("Server filename is not a regular filename")

    root = cache_directory.resolve()
    destination = (root / server_filename).resolve()
    if destination.parent != root:
        raise DownloadIntegrityError("Download destination escapes the cache directory")
    return destination


def finalize_part(part_path: Path, destination: Path) -> None:
    """Flush a completed part file and atomically promote it to destination."""

    with part_path.open("rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    part_path.replace(destination)
