"""Minimal PNG signature and dimension validation."""

from __future__ import annotations

from pathlib import Path
import struct
import zlib

from blender_terrain.errors import DownloadIntegrityError

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def validate_png(path: Path, expected_width: int, expected_height: int) -> None:
    """Validate PNG chunks, checksums, termination, and requested dimensions."""

    with path.open("rb") as stream:
        if stream.read(8) != _PNG_SIGNATURE:
            raise DownloadIntegrityError("WMS response is not a PNG image")
        saw_header = False
        saw_image_data = False
        saw_end = False
        while not saw_end:
            chunk_header = stream.read(8)
            if len(chunk_header) != 8:
                raise DownloadIntegrityError("WMS PNG is truncated before its final chunk")
            chunk_length, chunk_type = struct.unpack(">I4s", chunk_header)
            checksum = zlib.crc32(chunk_type)
            remaining = chunk_length
            first_data = b""
            while remaining:
                data = stream.read(min(remaining, 1_048_576))
                if not data:
                    raise DownloadIntegrityError("WMS PNG contains a truncated chunk")
                if not first_data:
                    first_data = data
                checksum = zlib.crc32(data, checksum)
                remaining -= len(data)
            encoded_checksum = stream.read(4)
            if len(encoded_checksum) != 4:
                raise DownloadIntegrityError("WMS PNG contains a truncated checksum")
            if struct.unpack(">I", encoded_checksum)[0] != checksum:
                raise DownloadIntegrityError("WMS PNG contains a checksum mismatch")

            if not saw_header:
                if chunk_type != b"IHDR" or chunk_length != 13 or len(first_data) < 8:
                    raise DownloadIntegrityError("WMS PNG has no valid IHDR chunk")
                width, height = struct.unpack(">II", first_data[:8])
                if width != expected_width or height != expected_height:
                    raise DownloadIntegrityError("WMS PNG dimensions do not match the request")
                saw_header = True
            elif chunk_type == b"IDAT":
                saw_image_data = True
            elif chunk_type == b"IEND":
                if chunk_length != 0:
                    raise DownloadIntegrityError("WMS PNG has an invalid final chunk")
                saw_end = True
        if not saw_image_data or stream.read(1):
            raise DownloadIntegrityError("WMS PNG has incomplete or trailing image data")
