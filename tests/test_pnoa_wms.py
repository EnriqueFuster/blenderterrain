from __future__ import annotations

import math
import struct
import unittest
import zlib
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

from blender_terrain.errors import DownloadIntegrityError, ProviderContractChanged
from blender_terrain.io.png_validation import validate_png
from blender_terrain.io.wms_capabilities import WMSCapabilities, parse_wms_capabilities
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.pnoa_wms import PNOAWMSClient, _validate_map_request

CAPABILITIES = b"""<?xml version="1.0"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
  <Service><MaxWidth>4096</MaxWidth><MaxHeight>4096</MaxHeight></Service>
  <Capability>
    <Request><GetMap><Format>image/png</Format><Format>image/jpeg</Format></GetMap></Request>
    <Layer><CRS>EPSG:25830</CRS><Layer><Name>OI.OrthoimageCoverage</Name></Layer></Layer>
  </Capability>
</WMS_Capabilities>"""


class WMSCapabilitiesTests(unittest.TestCase):
    def test_reads_inherited_crs_and_limits(self) -> None:
        capabilities = parse_wms_capabilities(CAPABILITIES, "OI.OrthoimageCoverage")

        self.assertEqual(capabilities.version, "1.3.0")
        self.assertEqual(capabilities.crs, ("EPSG:25830",))
        self.assertEqual(capabilities.max_width, 4096)
        self.assertEqual(capabilities.max_height, 4096)

    def test_rejects_a_missing_png_format(self) -> None:
        xml = CAPABILITIES.replace(b"image/png", b"image/tiff")

        with self.assertRaisesRegex(ProviderContractChanged, "PNG"):
            parse_wms_capabilities(xml, "OI.OrthoimageCoverage")


class WMSMapRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bounds = ProjectedBounds(713500, 4374500, 713628, 4374628, 25830)
        self.capabilities = WMSCapabilities(
            "1.3.0", "OI.OrthoimageCoverage", ("image/png",), ("EPSG:25830",), 4096, 4096
        )

    def test_uses_projected_easting_northing_axis_order(self) -> None:
        query = parse_qs(urlparse(PNOAWMSClient._map_url(self.bounds, 512, 512)).query)

        self.assertEqual(query["CRS"], ["EPSG:25830"])
        self.assertEqual(query["BBOX"], ["713500,4374500,713628,4374628"])
        self.assertEqual(query["WIDTH"], ["512"])
        self.assertEqual(query["HEIGHT"], ["512"])

    def test_rejects_dimensions_above_advertised_limit(self) -> None:
        with self.assertRaisesRegex(ValueError, "advertised"):
            _validate_map_request(self.bounds, 4097, 512, self.capabilities)

    def test_rejects_an_unverified_axis_order(self) -> None:
        bounds = ProjectedBounds(-3, 39, -2, 40, 4326)

        with self.assertRaisesRegex(ValueError, "axis order"):
            _validate_map_request(bounds, 512, 512, self.capabilities)

    def test_accepts_all_advertised_spanish_etrs89_utm_zones(self) -> None:
        for epsg in (25828, 25829, 25830, 25831):
            with self.subTest(epsg=epsg):
                capabilities = WMSCapabilities(
                    "1.3.0",
                    "OI.OrthoimageCoverage",
                    ("image/png",),
                    (f"EPSG:{epsg}",),
                    4096,
                    4096,
                )
                bounds = ProjectedBounds(100, 200, 300, 400, epsg)

                _validate_map_request(bounds, 512, 512, capabilities)

    def test_rejects_non_finite_projected_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            ProjectedBounds(math.nan, 4374500, 713628, 4374628, 25830)


class PNGValidationTests(unittest.TestCase):
    def test_accepts_matching_ihdr_dimensions(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "map.png"
            path.write_bytes(_png_file(512, 256))

            validate_png(path, 512, 256)

    def test_rejects_different_dimensions(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "map.png"
            path.write_bytes(_png_file(256, 256))

            with self.assertRaisesRegex(DownloadIntegrityError, "dimensions"):
                validate_png(path, 512, 256)

    def test_rejects_a_truncated_image(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "map.png"
            path.write_bytes(_png_file(512, 256)[:-5])

            with self.assertRaisesRegex(DownloadIntegrityError, "truncated"):
                validate_png(path, 512, 256)


def _png_file(width: int, height: int) -> bytes:
    header_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", header_data) + _png_chunk(
        b"IDAT", b"test"
    ) + _png_chunk(b"IEND", b"")


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    checksum = zlib.crc32(data, zlib.crc32(chunk_type))
    return struct.pack(">I4s", len(data), chunk_type) + data + struct.pack(">I", checksum)
