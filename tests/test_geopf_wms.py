from __future__ import annotations

import hashlib
import io
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import numpy as np
import pytest

from blender_terrain.catalog import load_bundled_catalog
from blender_terrain.core.grid import GridSpec
from blender_terrain.errors import ProviderContractChanged, RasterFormatError
from blender_terrain.io.bil32 import (
    Bil32Metadata,
    Bil32WindowReader,
    validate_bil32_payload,
    write_bil32_metadata,
)
from blender_terrain.io.png_validation import write_rgb_png
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.geopf_wms import (
    GeopfElevationRequest,
    GeopfWMSClient,
    plan_geopf_elevation_requests,
    verify_geopf_request_overlaps,
)

CAPABILITIES = b"""<?xml version="1.0"?>
<WMS_Capabilities xmlns="http://www.opengis.net/wms" version="1.3.0">
  <Service><MaxWidth>5010</MaxWidth><MaxHeight>5010</MaxHeight></Service>
  <Capability>
    <Request><GetMap><Format>image/x-bil;bits=32</Format></GetMap></Request>
    <Layer><CRS>EPSG:2154</CRS>
      <Layer><Name>ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES</Name></Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>"""


class Response:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.stream = io.BytesIO(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))

    def read(self, amount: int = -1) -> bytes:
        return self.stream.read(amount)

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> None:
        pass


class Opener:
    def __init__(self, responses: list[Response | Exception]) -> None:
        self.responses = responses
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float) -> Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_splits_master_grid_into_node_aligned_overlapping_requests() -> None:
    grid = GridSpec(ProjectedBounds(0.0, 0.0, 6.0, 2.0, 2154), 1.0, 6, 2)

    requests = plan_geopf_elevation_requests(grid, maximum_dimension=4)

    assert [(request.width, request.height) for request in requests] == [(4, 3), (4, 3)]
    assert requests[0].bounds == ProjectedBounds(-0.5, -0.5, 3.5, 2.5, 2154)
    assert requests[1].bounds == ProjectedBounds(2.5, -0.5, 6.5, 2.5, 2154)
    first_centres = np.linspace(
        requests[0].bounds.west + 0.5,
        requests[0].bounds.east - 0.5,
        requests[0].width,
    )
    second_centres = np.linspace(
        requests[1].bounds.west + 0.5,
        requests[1].bounds.east - 0.5,
        requests[1].width,
    )
    assert first_centres[-1] == second_centres[0]


def test_validates_bil32_length_endian_and_non_finite_values() -> None:
    values = np.asarray([[1.0, 2.0], [-99999.0, 4.0]], dtype="<f4")

    actual = validate_bil32_payload(values.tobytes(), 2, 2, -99999.0)

    np.testing.assert_array_equal(actual, values)
    with pytest.raises(ProviderContractChanged, match="length"):
        validate_bil32_payload(values.tobytes()[:-1], 2, 2, -99999.0)
    values[0, 0] = np.nan
    with pytest.raises(ProviderContractChanged, match="non-finite"):
        validate_bil32_payload(values.tobytes(), 2, 2, -99999.0)


def test_reads_cached_bil_using_its_provenance(tmp_path: Path) -> None:
    path = tmp_path / "window.bil"
    values = np.arange(12, dtype="<f4").reshape(3, 4)
    path.write_bytes(values.tobytes())
    write_bil32_metadata(path, _metadata(path, values))

    reader = Bil32WindowReader(path, verify_hash=True)
    actual, bounds = reader.read_bounds(ProjectedBounds(0.0, 0.0, 3.0, 2.0, 2154))

    np.testing.assert_array_equal(actual, values)
    assert bounds == ProjectedBounds(-0.5, -0.5, 3.5, 2.5, 2154)


def test_downloads_validated_bil_and_reuses_cache_without_network(tmp_path: Path) -> None:
    product = load_bundled_catalog().product("FR_RGE_ALTI_1M")
    values = np.arange(12, dtype="<f4").reshape(3, 4)
    opener = Opener(
        [
            Response(CAPABILITIES, "application/xml"),
            Response(values.tobytes(), "image/x-bil;bits=32"),
        ]
    )
    client = GeopfWMSClient(product, opener=opener)
    request = GeopfElevationRequest(
        ProjectedBounds(-0.5, -0.5, 3.5, 2.5, 2154), 4, 3, 0, 0
    )

    path, cached = client.download_bil(request, tmp_path)
    second_client = GeopfWMSClient(product, opener=Opener([]))
    reused_path, reused = second_client.download_bil(request, tmp_path)

    assert not cached
    assert reused
    assert reused_path == path
    assert len(opener.requests) == 2
    assert "BBOX=-0.5%2C-0.5%2C3.5%2C2.5" in opener.requests[1].full_url
    Bil32WindowReader(path, verify_hash=True)


def test_downloads_bd_ortho_png_and_reuses_validated_cache(tmp_path: Path) -> None:
    product = load_bundled_catalog().product("FR_BD_ORTHO")
    source = tmp_path / "source.png"
    write_rgb_png(source, np.arange(36, dtype=np.uint8).reshape(3, 4, 3))
    capabilities = CAPABILITIES.replace(
        b"ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES",
        b"HR.ORTHOIMAGERY.ORTHOPHOTOS",
    ).replace(b"image/x-bil;bits=32", b"image/png")
    opener = Opener(
        [Response(capabilities, "application/xml"), Response(source.read_bytes(), "image/png")]
    )
    client = GeopfWMSClient(product, opener=opener)
    bounds = ProjectedBounds(0.0, 0.0, 4.0, 3.0, 2154)

    path, cached = client.download_png(bounds, 4, 3, tmp_path / "cache")
    reused, reused_cached = GeopfWMSClient(product, opener=Opener([])).download_png(
        bounds, 4, 3, tmp_path / "cache"
    )

    assert not cached
    assert reused_cached
    assert reused == path
    assert path.with_suffix(".png.json").is_file()


def test_rejects_corrupt_cached_bil(tmp_path: Path) -> None:
    path = tmp_path / "window.bil"
    values = np.arange(12, dtype="<f4").reshape(3, 4)
    path.write_bytes(values.tobytes())
    write_bil32_metadata(path, _metadata(path, values))
    path.write_bytes(b"\x00" * len(values.tobytes()))

    with pytest.raises(RasterFormatError, match="hash"):
        Bil32WindowReader(path, verify_hash=True)


def test_retries_transient_wms_failure_and_respects_retry_after(tmp_path: Path) -> None:
    product = load_bundled_catalog().product("FR_RGE_ALTI_1M")
    values = np.arange(12, dtype="<f4").reshape(3, 4)
    headers = Message()
    headers["Retry-After"] = "1"
    unavailable = HTTPError("https://data.geopf.fr", 503, "Unavailable", headers, None)
    opener = Opener(
        [
            Response(CAPABILITIES, "application/xml"),
            unavailable,
            Response(values.tobytes(), "image/x-bil;bits=32"),
        ]
    )
    delays: list[float] = []
    client = GeopfWMSClient(product, opener=opener, sleeper=delays.append)
    request = GeopfElevationRequest(
        ProjectedBounds(-0.5, -0.5, 3.5, 2.5, 2154), 4, 3, 0, 0
    )

    path, cached = client.download_bil(request, tmp_path)

    assert path.is_file()
    assert not cached
    assert delays == [1.0]


def test_requires_identical_shared_nodes_between_wms_blocks(tmp_path: Path) -> None:
    requests = (
        GeopfElevationRequest(ProjectedBounds(-0.5, -0.5, 3.5, 2.5, 2154), 4, 3, 0, 0),
        GeopfElevationRequest(ProjectedBounds(2.5, -0.5, 6.5, 2.5, 2154), 4, 3, 0, 3),
    )
    left = np.arange(12, dtype="<f4").reshape(3, 4)
    right = np.arange(12, 24, dtype="<f4").reshape(3, 4)
    right[:, 0] = left[:, -1]
    paths = (tmp_path / "left.bil", tmp_path / "right.bil")
    paths[0].write_bytes(left.tobytes())
    paths[1].write_bytes(right.tobytes())

    verify_geopf_request_overlaps(paths, requests)
    right[1, 0] += 1.0
    paths[1].write_bytes(right.tobytes())

    with pytest.raises(ProviderContractChanged, match="columns"):
        verify_geopf_request_overlaps(paths, requests)


def _metadata(path: Path, values: np.ndarray) -> Bil32Metadata:
    return Bil32Metadata(
        "ign_france",
        "FR_RGE_ALTI_1M",
        "https://data.geopf.fr/wms-r/wms",
        "1.3.0",
        "ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES",
        "normal",
        "image/x-bil;bits=32",
        2154,
        (-0.5, -0.5, 3.5, 2.5),
        values.shape[1],
        values.shape[0],
        "<f4",
        "north_to_south",
        -99999.0,
        "2026-09-01T00:00:00+00:00",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )
