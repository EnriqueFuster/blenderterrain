"""Safe, bounded file writing for downloaded source resources."""

from __future__ import annotations

import os
from pathlib import Path

from ..errors import DownloadIntegrityError


def normalized_server_filename(filename: str) -> str:
    """Normalize observed case and separator differences between CNIG systems."""

    return filename.replace("_", "-").casefold()


def safe_destination(cache_directory: Path, server_filename: str) -> Path:
    """Return a destination inside cache_directory or reject unsafe names."""

    if (
        not server_filename
        or Path(server_filename).name != server_filename
        or "/" in server_filename
        or "\\" in server_filename
    ):
        raise DownloadIntegrityError("Server filename is empty or contains a path component")
    if (
        server_filename in {".", ".."}
        or server_filename.rstrip(" .") != server_filename
        or any(ord(character) < 32 or character in ':<>"|?*' for character in server_filename)
    ):
        raise DownloadIntegrityError("Server filename is not a regular filename")
    windows_stem = server_filename.split(".", maxsplit=1)[0].upper()
    reserved_names = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    if windows_stem in reserved_names:
        raise DownloadIntegrityError("Server filename is reserved by the operating system")

    root = cache_directory.resolve()
    destination = (root / server_filename).resolve()
    if destination.parent != root:
        raise DownloadIntegrityError("Download destination escapes the cache directory")
    return destination


def finalize_part(part_path: Path, destination: Path) -> None:
    """Flush and promote a part file without overwriting an existing resource."""

    if part_path.parent.resolve() != destination.parent.resolve():
        raise DownloadIntegrityError("Partial and final files must share a directory")
    with part_path.open("rb+") as stream:
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(part_path, destination)
    except FileExistsError as exc:
        raise DownloadIntegrityError("Destination appeared during download finalization") from exc
    part_path.unlink()
