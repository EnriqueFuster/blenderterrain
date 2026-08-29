from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from blender_terrain.catalog import (
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionMode,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import DownloadIntegrityError, RasterFormatError
from blender_terrain.providers.gebco import (
    GebcoAcquirer,
    gebco_query_urls,
    parse_opendap_ascii,
)

ROI = BBoxWGS84(-1.0, 50.0, -0.99, 50.01)


def _response(variable: str, values: tuple[tuple[int, ...], ...]) -> bytes:
    rows = len(values)
    columns = len(values[0])
    matrix = "\n".join(
        f"[{index}], " + ", ".join(str(value) for value in row)
        for index, row in enumerate(values)
    )
    return (
        f"Dataset {{ Grid {{ }} {variable}; }} dataset;\n"
        "---------------------------------------------\n"
        f"{variable}.{variable}[{rows}][{columns}]\n{matrix}\n\n"
        f"{variable}.lat[{rows}]\n"
        "50.00208333333333,50.00625,50.01041666666666\n\n"
        f"{variable}.lon[{columns}]\n"
        "-0.9979166666666686,-0.99375,-0.9895833333333428\n"
    ).encode("ascii")


def test_parses_south_to_north_response_as_north_up_grid() -> None:
    data, bounds = parse_opendap_ascii(
        _response("elevation", ((-30, -29, -28), (-20, -19, -18), (-10, -9, -8))),
        "elevation",
        (3, 3),
    )

    np.testing.assert_array_equal(
        data,
        np.asarray(((-10, -9, -8), (-20, -19, -18), (-30, -29, -28)), np.float32),
    )
    assert bounds.epsg == 4326
    assert bounds.west == pytest.approx(-1.0)
    assert bounds.south == pytest.approx(50.0)
    assert bounds.east == pytest.approx(-0.9875)
    assert bounds.north == pytest.approx(50.0125)


def test_acquires_aligned_elevation_and_tid_and_reuses_cache(tmp_path: Path) -> None:
    calls: list[str] = []

    def fetch(url: str, maximum_bytes: int) -> bytes:
        calls.append(url)
        assert maximum_bytes > 0
        if "?elevation" in url:
            return _response("elevation", ((-30, -29, -28), (-20, -19, -18), (-10, -9, -8)))
        return _response("tid", ((40, 40, 40), (41, 41, 41), (11, 11, 11)))

    selection = ProductSelection(
        "gebco", "GEBCO_2026", DatasetKind.BATHYMETRY, SelectionMode.MANUAL, True
    )
    request = LayerRequest(DatasetKind.BATHYMETRY, 463.0)
    acquirer = GebcoAcquirer(fetch)

    result = acquirer.acquire(selection, request, ROI, tmp_path)
    cached = acquirer.acquire(selection, request, ROI, tmp_path)

    assert len(calls) == 2
    assert result.kind is DatasetKind.BATHYMETRY
    assert result.paths[0].name == "bathymetry.npy"
    assert result.auxiliary_paths[0].name == "tid.npy"
    assert cached.cached_count == 1
    np.testing.assert_array_equal(
        np.load(result.auxiliary_paths[0]),
        ((11, 11, 11), (41, 41, 41), (40, 40, 40)),
    )


def test_rejects_malformed_or_excessive_windows(tmp_path: Path) -> None:
    with pytest.raises(DownloadIntegrityError, match="unexpected array shape"):
        parse_opendap_ascii(b"not a grid", "elevation", (3, 3))

    selection = ProductSelection(
        "gebco", "GEBCO_2026", DatasetKind.BATHYMETRY, SelectionMode.MANUAL, True
    )
    with pytest.raises(RasterFormatError, match="cell limit"):
        GebcoAcquirer(lambda *_: b"").acquire(
            selection,
            LayerRequest(DatasetKind.BATHYMETRY),
            BBoxWGS84(-10.0, 30.0, 10.0, 50.0),
            tmp_path,
        )


def test_tiny_roi_is_expanded_to_minimum_resample_window() -> None:
    urls, shape = gebco_query_urls(BBoxWGS84(-1.0, 50.0, -0.999, 50.001))

    assert shape == (2, 2)
    assert "elevation[33600:1:33601][42960:1:42961]" in urls["elevation"]
