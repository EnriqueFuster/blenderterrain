from __future__ import annotations

import argparse

import pytest

from scripts.probe_global_sources import (
    _bbox,
    _content_range_total,
    _jrc_tile,
    _tiff_variant,
    gebco_query_urls,
    source_urls,
)


def test_builds_valencia_global_source_urls() -> None:
    urls = source_urls((-0.39, 39.46, -0.37, 39.48))

    assert "N39_00_W001_00" in urls["copernicus_glo30"]
    assert "rgbnir/2021/N39/" in urls["worldcover_s2_2021"]
    assert "N39W001_S2RGBNIR.tif" in urls["worldcover_s2_2021"]
    assert urls["gedtm_elevation"].endswith("v20250611.tif")
    assert urls["jrc_gsw_occurrence"].endswith(
        "occurrence_10W_40N_v1_5_2024.tif"
    )
    assert urls["jrc_gsw_extent"].endswith("extent_10W_40N_v1_5_2024.tif")


def test_rejects_probe_bbox_crossing_source_tiles() -> None:
    with pytest.raises(ValueError, match="one degree of longitude"):
        source_urls((-0.1, 39.4, 0.1, 39.5))


def test_builds_southern_and_eastern_tile_names() -> None:
    urls = source_urls((2.1, -34.9, 2.2, -34.8))

    assert "S35_00_E002_00" in urls["copernicus_glo30"]
    assert "rgbnir/2021/S35/" in urls["worldcover_s2_2021"]
    assert "S35E002_S2RGBNIR.tif" in urls["worldcover_s2_2021"]
    assert urls["jrc_gsw_occurrence"].endswith(
        "occurrence_0E_30S_v1_5_2024.tif"
    )


def test_jrc_tile_uses_west_and_north_edges() -> None:
    assert _jrc_tile(-0.39, 39.46, -0.37, 39.48) == "10W_40N"
    assert _jrc_tile(2.1, -34.9, 2.2, -34.8) == "0E_30S"
    assert _jrc_tile(0.0, 0.0, 10.0, 10.0) == "0E_10N"
    with pytest.raises(ValueError, match="one JRC"):
        _jrc_tile(-0.1, 39.0, 0.1, 39.1)


def test_accepts_a_bbox_ending_exactly_at_a_tile_boundary() -> None:
    urls = source_urls((0.0, 39.0, 1.0, 40.0))

    assert "N39_00_E000_00" in urls["copernicus_glo30"]


def test_validates_bbox_text() -> None:
    assert _bbox("-0.39,39.46,-0.37,39.48") == (-0.39, 39.46, -0.37, 39.48)
    with pytest.raises(argparse.ArgumentTypeError):
        _bbox("0,1,2")
    with pytest.raises(argparse.ArgumentTypeError):
        _bbox("2,0,1,1")


def test_parses_bounded_range_contract() -> None:
    assert _content_range_total("bytes 0-65535/333361957920") == 333_361_957_920
    with pytest.raises(RuntimeError, match="Invalid Content-Range"):
        _content_range_total("333361957920")


def test_identifies_tiff_headers() -> None:
    assert _tiff_variant(b"II*\x00rest") == "TIFF"
    assert _tiff_variant(b"II+\x00rest") == "BigTIFF"
    with pytest.raises(RuntimeError, match="TIFF signature"):
        _tiff_variant(b"<html")


def test_builds_aligned_bounded_gebco_queries() -> None:
    urls, shape = gebco_query_urls((-1.0, 50.0, -0.99, 50.01))

    assert shape == (3, 3)
    assert "elevation[33600:1:33602][42960:1:42962]" in urls["elevation"]
    assert "tid[33600:1:33602][42960:1:42962]" in urls["tid"]
    assert urls["elevation"].startswith("https://dap.ceda.ac.uk/thredds/dodsC/")
