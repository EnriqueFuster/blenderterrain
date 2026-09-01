"""Minimal PNG signature and dimension validation."""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from ..errors import DownloadIntegrityError
from .atomic import finalize_part

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def write_rgb_png(path: Path, pixels: NDArray[np.uint8]) -> None:
    """Atomically write an unfiltered eight-bit RGB PNG."""

    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("PNG pixels must be an HxWx3 UInt8 array")
    height, width, _ = pixels.shape
    if width <= 0 or height <= 0:
        raise ValueError("PNG dimensions must be positive")
    rows = b"".join(b"\x00" + row.tobytes() for row in pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    encoded = (
        _PNG_SIGNATURE
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        finalize_part(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(payload, zlib.crc32(kind))
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def read_png_dimensions(path: Path) -> tuple[int, int]:
    """Read validated positive PNG dimensions from its fixed IHDR prefix."""

    with path.open("rb") as stream:
        prefix = stream.read(24)
    if (
        len(prefix) != 24
        or prefix[:8] != _PNG_SIGNATURE
        or prefix[12:16] != b"IHDR"
    ):
        raise DownloadIntegrityError("Image is not a valid PNG file")
    width, height = struct.unpack(">II", prefix[16:24])
    if width <= 0 or height <= 0:
        raise DownloadIntegrityError("PNG dimensions must be positive")
    return width, height


def read_rgb_png(path: Path) -> NDArray[np.uint8]:
    """Decode a non-interlaced eight-bit RGB or RGBA PNG into RGB pixels."""

    encoded = path.read_bytes()
    if encoded[:8] != _PNG_SIGNATURE:
        raise DownloadIntegrityError("Image is not a valid PNG file")
    position = 8
    header: tuple[int, ...] | None = None
    image_data = bytearray()
    while position + 12 <= len(encoded):
        length, kind = struct.unpack(">I4s", encoded[position : position + 8])
        payload_start = position + 8
        payload_end = payload_start + length
        if payload_end + 4 > len(encoded):
            raise DownloadIntegrityError("PNG chunk is truncated")
        payload = encoded[payload_start:payload_end]
        expected_crc = struct.unpack(">I", encoded[payload_end : payload_end + 4])[0]
        if zlib.crc32(payload, zlib.crc32(kind)) != expected_crc:
            raise DownloadIntegrityError("PNG contains a checksum mismatch")
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            image_data.extend(payload)
        elif kind == b"IEND":
            break
        position = payload_end + 4
    if header is None or not image_data:
        raise DownloadIntegrityError("PNG has no header or image data")
    width, height, depth, color_type, compression, filter_method, interlace = header
    if depth != 8 or color_type not in {2, 6} or (compression, filter_method, interlace) != (
        0,
        0,
        0,
    ):
        raise DownloadIntegrityError("PNG layout is unsupported for GeoTIFF export")
    channels = 3 if color_type == 2 else 4
    stride = width * channels
    try:
        raw = zlib.decompress(bytes(image_data))
    except zlib.error as exc:
        raise DownloadIntegrityError("PNG image data is invalid") from exc
    if len(raw) != height * (stride + 1):
        raise DownloadIntegrityError("PNG image data has unexpected dimensions")
    rows = np.empty((height, stride), dtype=np.uint8)
    previous: NDArray[np.uint8] = np.zeros(stride, dtype=np.uint8)
    offset = 0
    for row_index in range(height):
        filter_type = raw[offset]
        scanline = np.frombuffer(raw, dtype=np.uint8, count=stride, offset=offset + 1).copy()
        _undo_png_filter(scanline, previous, channels, filter_type)
        rows[row_index] = scanline
        previous = scanline
        offset += stride + 1
    return rows.reshape(height, width, channels)[:, :, :3].copy()


def _undo_png_filter(
    row: NDArray[np.uint8], previous: NDArray[np.uint8], channels: int, filter_type: int
) -> None:
    if filter_type == 0:
        return
    for index in range(len(row)):
        left = int(row[index - channels]) if index >= channels else 0
        above = int(previous[index])
        upper_left = int(previous[index - channels]) if index >= channels else 0
        if filter_type == 1:
            prediction = left
        elif filter_type == 2:
            prediction = above
        elif filter_type == 3:
            prediction = (left + above) // 2
        elif filter_type == 4:
            prediction = _paeth(left, above, upper_left)
        else:
            raise DownloadIntegrityError("PNG uses an unknown row filter")
        row[index] = (int(row[index]) + prediction) & 0xFF


def _paeth(left: int, above: int, upper_left: int) -> int:
    estimate = left + above - upper_left
    distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
    return (left, above, upper_left)[distances.index(min(distances))]


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
