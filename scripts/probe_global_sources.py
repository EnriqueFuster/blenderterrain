"""Probe the three global MVP data sources with bounded read-only requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = "BlenderTerrain/0.4-source-probe"
RANGE_BYTES = 65_536
GEDTM_VERSION = "v20250611"
GEDTM_BASE = "https://s3.opengeohub.org/global/edtm"
GEDTM_COLLECTION_URL = (
    "https://s3.eu-central-1.wasabisys.com/stac/openlandmap/gedtm-30m/collection.json"
)
GEDTM_ITEM_URL = (
    "https://s3.eu-central-1.wasabisys.com/stac/openlandmap/gedtm-30m/"
    "gedtm-30m_20060101_20151231/gedtm-30m_20060101_20151231.json"
)
GLO30_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
WORLDCOVER_BASE = "https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com"
JRC_GSW_BASE = (
    "https://s3.waw4-1.cloudferro.com/swift/v1/global-surface-water/"
    "download2024/Aggregated/VER1-5"
)
GEBCO_DAP_BASE = (
    "https://dap.ceda.ac.uk/thredds/dodsC/bodc/gebco/global/gebco_2026"
)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bbox",
        required=True,
        type=_bbox,
        metavar="WEST,SOUTH,EAST,NORTH",
        help="Small WGS84 ROI contained in one one-degree source tile",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--inspect-raster",
        action="store_true",
        help="Use the optional Rasterio oracle to inspect and read the ROI",
    )
    arguments = parser.parse_args()
    report = probe_sources(arguments.bbox, inspect_raster=arguments.inspect_raster)
    output = arguments.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(output)


def probe_sources(
    bbox: tuple[float, float, float, float], *, inspect_raster: bool
) -> dict[str, Any]:
    """Return reproducible metadata from bounded requests for one small ROI."""

    assets = source_urls(bbox)
    collection = _read_json(GEDTM_COLLECTION_URL, 1_048_576)
    item = _read_json(GEDTM_ITEM_URL, 1_048_576)
    catalog_hrefs = sorted(
        asset["href"]
        for asset in item.get("assets", {}).values()
        if isinstance(asset, dict) and isinstance(asset.get("href"), str)
    )
    results: dict[str, Any] = {}
    for name, url in assets.items():
        result = _probe_range(url)
        if inspect_raster:
            result["raster"] = _inspect_with_rasterio(url, bbox)
        results[name] = result
    gebco_urls, gebco_shape = gebco_query_urls(bbox)
    results["gebco_elevation"] = _probe_opendap(
        gebco_urls["elevation"], "elevation", gebco_shape
    )
    results["gebco_tid"] = _probe_opendap(gebco_urls["tid"], "tid", gebco_shape)
    return {
        "bbox_wgs84": list(bbox),
        "gedtm_collection": {
            "id": collection.get("id"),
            "stac_version": collection.get("stac_version"),
            "version": collection.get("version"),
            "license": collection.get("license"),
            "spatial_extent": collection.get("extent", {}).get("spatial", {}).get("bbox"),
            "source_url": GEDTM_COLLECTION_URL,
            "item_url": GEDTM_ITEM_URL,
            "catalog_asset_hrefs": catalog_hrefs,
            "catalog_references_verified_v11": all(
                GEDTM_VERSION in href for href in catalog_hrefs if href.endswith(".tif")
            ),
        },
        "assets": results,
    }


def gebco_query_urls(
    bbox: tuple[float, float, float, float],
) -> tuple[dict[str, str], tuple[int, int]]:
    """Build aligned GEBCO elevation and TID subset queries for a WGS84 bbox."""

    west, south, east, north = bbox
    column_start = max(0, math.floor((west + 180.0) * 240.0))
    column_end = min(86_399, math.ceil((east + 180.0) * 240.0) - 1)
    row_start = max(0, math.floor((south + 90.0) * 240.0))
    row_end = min(43_199, math.ceil((north + 90.0) * 240.0) - 1)
    if row_end < row_start or column_end < column_start:
        raise ValueError("GEBCO bbox does not contain a grid cell")
    section = f"[{row_start}:1:{row_end}][{column_start}:1:{column_end}]"
    return (
        {
            "elevation": (
                f"{GEBCO_DAP_BASE}/ice_surface_elevation/netcdf/"
                f"GEBCO_2026.nc.ascii?elevation{section}"
            ),
            "tid": (
                f"{GEBCO_DAP_BASE}/type_identifier_grid/netcdf/"
                f"gebco_2026_tid.nc.ascii?tid{section}"
            ),
        },
        (row_end - row_start + 1, column_end - column_start + 1),
    )


def _probe_opendap(
    url: str, variable: str, expected_shape: tuple[int, int]
) -> dict[str, Any]:
    maximum_bytes = 2_000_000
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with build_opener(_NoRedirects()).open(request, timeout=30) as response:
        payload = response.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise RuntimeError("GEBCO probe response exceeds the bounded size")
    shape = f"{variable}.{variable}[{expected_shape[0]}][{expected_shape[1]}]".encode()
    if response.status != 200 or shape not in payload:
        raise RuntimeError("GEBCO OPeNDAP response does not match the requested window")
    return {
        "url": url,
        "status": response.status,
        "rows": expected_shape[0],
        "columns": expected_shape[1],
        "response_bytes": len(payload),
        "response_sha256": hashlib.sha256(payload).hexdigest(),
    }


def source_urls(bbox: tuple[float, float, float, float]) -> dict[str, str]:
    """Build the verified assets covering a bbox contained in one degree tile."""

    west, south, east, north = bbox
    longitude = _single_degree(west, east, "longitude")
    latitude = _single_degree(south, north, "latitude")
    glo_tile = f"{_hemisphere(latitude, 'N', 'S', 2)}_00_{_hemisphere(longitude, 'E', 'W', 3)}_00"
    glo_name = f"Copernicus_DSM_COG_10_{glo_tile}_DEM"
    worldcover_tile = (
        f"{_hemisphere(latitude, 'N', 'S', 2)}{_hemisphere(longitude, 'E', 'W', 3)}"
    )
    worldcover_name = f"ESA_WorldCover_10m_2021_v200_{worldcover_tile}_S2RGBNIR.tif"
    jrc_tile = _jrc_tile(west, south, east, north)
    return {
        "gedtm_elevation": (
            f"{GEDTM_BASE}/gedtm_rf_m_30m_s_20060101_20151231_go_"
            f"epsg.4326.3855_{GEDTM_VERSION}.tif"
        ),
        "gedtm_uncertainty": (
            f"{GEDTM_BASE}/gedtm_rf_std_30m_s_20060101_20151231_go_"
            f"epsg.4326.3855_{GEDTM_VERSION}.tif"
        ),
        "gedtm_selection_mask": (
            f"{GEDTM_BASE}/gedtm_mask_c_30m_s_20060101_20151231_go_"
            f"epsg.4326.3855_{GEDTM_VERSION}.tif"
        ),
        "copernicus_glo30": f"{GLO30_BASE}/{glo_name}/{glo_name}.tif",
        "worldcover_s2_2021": (
            f"{WORLDCOVER_BASE}/rgbnir/2021/"
            f"{_hemisphere(latitude, 'N', 'S', 2)}/{worldcover_name}"
        ),
        "jrc_gsw_occurrence": (
            f"{JRC_GSW_BASE}/occurrence/"
            f"occurrence_{jrc_tile}_v1_5_2024.tif"
        ),
        "jrc_gsw_extent": (
            f"{JRC_GSW_BASE}/extent/extent_{jrc_tile}_v1_5_2024.tif"
        ),
    }


def _probe_range(url: str) -> dict[str, Any]:
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Range": f"bytes=0-{RANGE_BYTES - 1}"},
    )
    with build_opener(_NoRedirects()).open(request, timeout=30) as response:
        payload = response.read(RANGE_BYTES + 1)
        status = response.status
        content_range = response.headers.get("Content-Range")
        content_type = response.headers.get_content_type()
    if status != 206 or len(payload) != RANGE_BYTES or content_range is None:
        raise RuntimeError(f"Source ignored the bounded Range request: {url}")
    total_bytes = _content_range_total(content_range)
    return {
        "url": url,
        "status": status,
        "content_type": content_type,
        "content_range": content_range,
        "total_bytes": total_bytes,
        "range_bytes": len(payload),
        "range_sha256": hashlib.sha256(payload).hexdigest(),
        "tiff_variant": _tiff_variant(payload),
    }


def _read_json(url: str, maximum_bytes: int) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with build_opener(_NoRedirects()).open(request, timeout=30) as response:
        payload = response.read(maximum_bytes + 1)
        content_type = response.headers.get_content_type()
    if len(payload) > maximum_bytes or content_type != "application/json":
        raise RuntimeError(f"Catalog response is not bounded JSON: {url}")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError(f"Catalog response is not a JSON object: {url}")
    return value


def _inspect_with_rasterio(
    url: str, bbox: tuple[float, float, float, float]
) -> dict[str, Any]:
    try:
        import numpy as np
        import rasterio  # type: ignore[import-untyped]
        from rasterio.windows import from_bounds  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError("Install the 'oracle' extra to use --inspect-raster") from exc

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(url) as dataset:
        window = from_bounds(*bbox, transform=dataset.transform).round_offsets().round_lengths()
        source_window = rasterio.windows.Window(0, 0, dataset.width, dataset.height)
        window = window.intersection(source_window)
        data = dataset.read(window=window, out_shape=(dataset.count, 32, 32), masked=True)
        valid = data.compressed()
        return {
            "driver": dataset.driver,
            "crs": None if dataset.crs is None else dataset.crs.to_string(),
            "width": dataset.width,
            "height": dataset.height,
            "bands": dataset.count,
            "dtypes": list(dataset.dtypes),
            "nodata": dataset.nodata,
            "scales": list(dataset.scales),
            "offsets": list(dataset.offsets),
            "compression": (
                None if dataset.compression is None else dataset.compression.value
            ),
            "color_interpretation": [value.name for value in dataset.colorinterp],
            "block_shapes": [list(shape) for shape in dataset.block_shapes],
            "overviews": [dataset.overviews(index) for index in dataset.indexes],
            "bounds": list(dataset.bounds),
            "transform": list(dataset.transform)[:6],
            "valid_samples": int(valid.size),
            "sample_min": None if not valid.size else float(np.min(valid)),
            "sample_max": None if not valid.size else float(np.max(valid)),
        }


def _bbox(value: str) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(component.strip()) for component in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox must contain four numbers") from exc
    if len(values) != 4:
        raise argparse.ArgumentTypeError("bbox must contain west,south,east,north")
    west, south, east, north = values
    if (
        not all(math.isfinite(coordinate) for coordinate in values)
        or not -180 <= west < east <= 180
        or not -90 <= south < north <= 90
    ):
        raise argparse.ArgumentTypeError("bbox coordinates or ordering are invalid")
    return west, south, east, north


def _single_degree(minimum: float, maximum: float, axis: str) -> int:
    first = math.floor(minimum)
    last = math.floor(math.nextafter(maximum, -math.inf))
    if first != last:
        raise ValueError(f"Probe bbox must remain inside one degree of {axis}")
    return first


def _hemisphere(value: int, positive: str, negative: str, width: int) -> str:
    return f"{positive if value >= 0 else negative}{abs(value):0{width}d}"


def _jrc_tile(west: float, south: float, east: float, north: float) -> str:
    """Return the JRC 10-degree tile named by its west and north edges."""

    longitude = math.floor(west / 10) * 10
    latitude = math.ceil(north / 10) * 10
    if east > longitude + 10 or south < latitude - 10:
        raise ValueError("Probe bbox must remain inside one JRC 10-degree tile")
    longitude_suffix = "E" if longitude >= 0 else "W"
    latitude_suffix = "N" if latitude >= 0 else "S"
    return f"{abs(longitude)}{longitude_suffix}_{abs(latitude)}{latitude_suffix}"


def _content_range_total(value: str) -> int:
    match = re.fullmatch(r"bytes\s+\d+-\d+/(\d+)", value)
    if match is None:
        raise RuntimeError(f"Invalid Content-Range header: {value}")
    return int(match.group(1))


def _tiff_variant(payload: bytes) -> str:
    if payload.startswith((b"II*\x00", b"MM\x00*")):
        return "TIFF"
    if payload.startswith((b"II+\x00", b"MM\x00+")):
        return "BigTIFF"
    raise RuntimeError("Range response does not start with a TIFF signature")


if __name__ == "__main__":
    main()
