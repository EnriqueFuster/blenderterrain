"""Offline safety checks for the Phase 0 single-file download path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from blender_terrain.errors import DownloadIntegrityError
from blender_terrain.io.atomic import finalize_part, safe_destination
from blender_terrain.io.tiff_validation import validate_tiff_signature


class DownloadSafetyTests(unittest.TestCase):
    def test_rejects_path_traversal_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with self.assertRaises(DownloadIntegrityError):
                safe_destination(Path(temporary_directory), "../escape.tif")

    def test_promotes_valid_bigtiff_part_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            destination = safe_destination(directory, "sample.tif")
            part = destination.with_name("sample.tif.part")
            part.write_bytes(b"II+\x00phase-0")

            self.assertEqual(validate_tiff_signature(part), "little-endian BigTIFF")
            finalize_part(part, destination)

            self.assertTrue(destination.is_file())
            self.assertFalse(part.exists())

    def test_rejects_non_tiff_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "not-a-tiff.bin"
            path.write_bytes(b"<html>")

            with self.assertRaises(DownloadIntegrityError):
                validate_tiff_signature(path)


if __name__ == "__main__":
    unittest.main()
