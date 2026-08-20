"""Offline safety checks for the single-file download path."""

from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.io.atomic import finalize_part, safe_destination
from blender_terrain.io.tiff_validation import validate_tiff_header
from blender_terrain.providers.cnig_portal import CNIGPortalClient


class DownloadSafetyTests(unittest.TestCase):
    def test_rejects_path_traversal_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            for filename in ("../escape.tif", "..\\escape.tif", "C:escape.tif"):
                with self.subTest(filename=filename), self.assertRaises(
                    DownloadIntegrityError
                ):
                    safe_destination(Path(temporary_directory), filename)

    def test_rejects_windows_reserved_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory, self.assertRaises(
            DownloadIntegrityError
        ):
            safe_destination(Path(temporary_directory), "CON.tif")

    def test_promotes_valid_bigtiff_part_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            destination = safe_destination(directory, "sample.tif")
            part = destination.with_name("sample.tif.part")
            part.write_bytes(
                b"II+\x00\x08\x00\x00\x00\x10\x00\x00\x00\x00\x00\x00\x00\x00"
            )

            self.assertEqual(validate_tiff_header(part), "little-endian BigTIFF")
            finalize_part(part, destination)

            self.assertTrue(destination.is_file())
            self.assertFalse(part.exists())

    def test_rejects_non_tiff_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "not-a-tiff.bin"
            path.write_bytes(b"<html>")

            with self.assertRaises(DownloadIntegrityError):
                validate_tiff_header(path)

    def test_rejects_bigtiff_signature_without_structural_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "truncated.tif"
            path.write_bytes(b"II+\x00")

            with self.assertRaises(DownloadIntegrityError):
                validate_tiff_header(path)

    def test_finalization_does_not_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            part = directory / "sample.tif.part"
            destination = directory / "sample.tif"
            part.write_bytes(b"new")
            destination.write_bytes(b"existing")

            with self.assertRaises(DownloadIntegrityError):
                finalize_part(part, destination)

            self.assertEqual(destination.read_bytes(), b"existing")

    def test_stream_writer_enforces_actual_byte_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            part = Path(temporary_directory) / "sample.tif.part"

            with self.assertRaises(DownloadIntegrityError):
                CNIGPortalClient._write_bounded_response(BytesIO(b"1234"), part, 3)

            self.assertFalse(part.exists())

    def test_stream_writer_removes_partial_file_after_read_failure(self) -> None:
        class FailingResponse:
            def __init__(self) -> None:
                self.calls = 0

            def read(self, amount: int = -1) -> bytes:
                self.calls += 1
                if self.calls == 1:
                    return b"partial"
                raise OSError("connection lost")

        with tempfile.TemporaryDirectory() as temporary_directory:
            part = Path(temporary_directory) / "sample.tif.part"

            with self.assertRaisesRegex(OSError, "connection lost"):
                CNIGPortalClient._write_bounded_response(FailingResponse(), part, 100)

            self.assertFalse(part.exists())


if __name__ == "__main__":
    unittest.main()
