"""Discovery of open Sentinel-2 L2A scenes through Earth Search STAC."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

import numpy as np
from numpy.typing import NDArray

from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import (
    JobCancelled,
    NoCoverageError,
    ProviderContractChanged,
    ProviderUnavailableError,
    RasterFormatError,
    UserInputError,
)
from ..io.atomic import finalize_part
from ..io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ..io.imagery_window import imagery_window_is_valid, write_imagery_window
from ..io.random_access import HttpRangeReader
from ..models import ProjectedBounds

PRODUCT_ID = "SENTINEL2_L2A"
PROVIDER_ID = "sentinel2"
STAC_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION_ID = "sentinel-2-c1-l2a"
_ASSET_HOST = "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"
_MAXIMUM_RESPONSE_BYTES = 5 * 1024 * 1024
_MAXIMUM_SOURCE_BYTES = 300_000_000
_MAXIMUM_WINDOW_PIXELS = 16_777_216
_SCENES_PER_GRID = 2
_GRID_CODE = re.compile(r"_T(?P<code>\d{2}[A-Z]{3})_")


def sentinel2_temporal_policy(
    start_date: str,
    end_date: str,
    maximum_cloud_percent: float,
    *,
    today: date | None = None,
) -> str:
    """Resolve UI dates into the immutable policy stored in an acquisition plan."""

    if not 0 <= maximum_cloud_percent <= 100:
        raise UserInputError("Sentinel-2 cloud cover must be between 0 and 100 percent")
    if bool(start_date) != bool(end_date):
        raise UserInputError("Provide both Sentinel-2 dates or leave both empty")
    if start_date:
        try:
            start = date.fromisoformat(start_date)
            end = date.fromisoformat(end_date)
        except ValueError as exc:
            raise UserInputError("Sentinel-2 dates must use YYYY-MM-DD") from exc
    else:
        end = today or datetime.now(UTC).date()
        start = end - timedelta(days=365)
    if start >= end:
        raise UserInputError("Sentinel-2 start date must precede its end date")
    return (
        f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z;"
        f"cloud={maximum_cloud_percent:g}"
    )


class _Response(Protocol):
    def read(self, amount: int = -1) -> bytes: ...
    def __enter__(self) -> _Response: ...
    def __exit__(self, *args: object) -> None: ...


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> _Response: ...


@dataclass(frozen=True, slots=True)
class Sentinel2Scene:
    """The metadata and COG assets needed to acquire one L2A scene."""

    id: str
    acquired_at: str
    cloud_cover_percent: float
    epsg: int
    bounds: BBoxWGS84
    red_url: str
    green_url: str
    blue_url: str
    scl_url: str
    red_scale: float = 1.0
    red_offset: float = 0.0
    green_scale: float = 1.0
    green_offset: float = 0.0
    blue_scale: float = 1.0
    blue_offset: float = 0.0

    @property
    def grid_code(self) -> str:
        """Return the MGRS tile encoded in the official scene identifier."""

        match = _GRID_CODE.search(self.id)
        if match is None:
            raise ProviderContractChanged("Sentinel-2 scene identifier has no grid code")
        return match.group("code")


def sentinel2_search_body(
    roi: BBoxWGS84,
    start: str,
    end: str,
    maximum_cloud_percent: float,
    *,
    limit: int = 20,
) -> bytes:
    """Build a bounded, deterministic STAC search for usable L2A scenes."""

    start_date = _parse_datetime(start)
    end_date = _parse_datetime(end)
    if start_date >= end_date:
        raise ValueError("Sentinel-2 search start must precede its end")
    if not 0 <= maximum_cloud_percent <= 100 or not 1 <= limit <= 100:
        raise ValueError("Sentinel-2 search limits are invalid")
    return json.dumps(
        {
            "collections": [COLLECTION_ID],
            "bbox": [roi.west, roi.south, roi.east, roi.north],
            "datetime": f"{start}/{end}",
            "query": {"eo:cloud_cover": {"lte": maximum_cloud_percent}},
            "sortby": [
                {"field": "properties.eo:cloud_cover", "direction": "asc"},
                {"field": "properties.datetime", "direction": "desc"},
            ],
            "limit": limit,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def parse_sentinel2_search(payload: bytes) -> tuple[Sentinel2Scene, ...]:
    """Validate the small part of the STAC response used by BlenderTerrain."""

    try:
        document = json.loads(payload)
        features = document["features"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ProviderContractChanged("Sentinel-2 STAC response schema changed") from exc
    if not isinstance(features, list):
        raise ProviderContractChanged("Sentinel-2 STAC response has no feature list")
    scenes = tuple(_parse_scene(feature) for feature in features)
    return tuple(
        sorted(
            scenes,
            key=lambda scene: (
                scene.cloud_cover_percent,
                -_parse_datetime(scene.acquired_at).timestamp(),
                scene.id,
            ),
        )
    )


def select_sentinel2_scenes(
    scenes: tuple[Sentinel2Scene, ...], roi: BBoxWGS84
) -> tuple[Sentinel2Scene, ...]:
    """Choose up to two clear acquisitions per grid tile for cloud filling."""

    counts: dict[str, int] = {}
    selected: list[Sentinel2Scene] = []
    for scene in scenes:
        if _intersection(scene.bounds, roi) is not None:
            count = counts.get(scene.grid_code, 0)
            if count < _SCENES_PER_GRID:
                selected.append(scene)
                counts[scene.grid_code] = count + 1
    result = tuple(selected)
    if not result or not _rectangles_cover(roi, tuple(scene.bounds for scene in result)):
        raise NoCoverageError("Sentinel-2 scenes do not cover the complete ROI")
    return result


class Sentinel2CatalogClient:
    """Query the public Earth Search catalogue without adding an STAC dependency."""

    def __init__(self, opener: _Opener | None = None) -> None:
        self._opener = opener or build_opener()

    def search(
        self,
        roi: BBoxWGS84,
        start: str,
        end: str,
        maximum_cloud_percent: float,
        *,
        limit: int = 20,
    ) -> tuple[Sentinel2Scene, ...]:
        request = Request(
            STAC_SEARCH_URL,
            data=sentinel2_search_body(roi, start, end, maximum_cloud_percent, limit=limit),
            headers={
                "User-Agent": "BlenderTerrain/0.5",
                "Content-Type": "application/json",
                "Accept": "application/geo+json,application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=30.0) as response:
                payload = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise ProviderUnavailableError("Sentinel-2 STAC search failed") from exc
        if len(payload) > _MAXIMUM_RESPONSE_BYTES:
            raise ProviderContractChanged("Sentinel-2 STAC response exceeds its size limit")
        return parse_sentinel2_search(payload)


def discover_sentinel2_scenes(
    roi: BBoxWGS84,
    temporal_policy: str | None,
    catalog: Sentinel2CatalogClient | None = None,
) -> tuple[Sentinel2Scene, ...]:
    """Resolve a policy and return the selected scenes that cover the ROI."""

    start, end, cloud = _temporal_policy(temporal_policy)
    scenes = (catalog or Sentinel2CatalogClient()).search(roi, start, end, cloud)
    if not scenes:
        raise NoCoverageError("Sentinel-2 has no scenes matching the temporal policy")
    return select_sentinel2_scenes(scenes, roi)


def _parse_scene(feature: Any) -> Sentinel2Scene:
    try:
        properties = feature["properties"]
        assets = feature["assets"]
        bbox = feature["bbox"]
        red_scale, red_offset = _asset_transform(assets["red"])
        green_scale, green_offset = _asset_transform(assets["green"])
        blue_scale, blue_offset = _asset_transform(assets["blue"])
        scene = Sentinel2Scene(
            id=feature["id"],
            acquired_at=properties["datetime"],
            cloud_cover_percent=float(properties["eo:cloud_cover"]),
            epsg=int(properties["proj:epsg"]),
            bounds=BBoxWGS84(*(float(value) for value in bbox)),
            red_url=assets["red"]["href"],
            green_url=assets["green"]["href"],
            blue_url=assets["blue"]["href"],
            scl_url=assets["scl"]["href"],
            red_scale=red_scale,
            red_offset=red_offset,
            green_scale=green_scale,
            green_offset=green_offset,
            blue_scale=blue_scale,
            blue_offset=blue_offset,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderContractChanged("Sentinel-2 STAC item schema changed") from exc
    _parse_datetime(scene.acquired_at)
    if (
        not scene.id
        or not scene.grid_code
        or not 0 <= scene.cloud_cover_percent <= 100
        or not 32601 <= scene.epsg <= 32760
        or any(not _valid_asset_url(url) for url in _scene_urls(scene))
    ):
        raise ProviderContractChanged("Sentinel-2 STAC item values are unsupported")
    return scene


def _asset_transform(asset: object) -> tuple[float, float]:
    if not isinstance(asset, dict):
        raise ProviderContractChanged("Sentinel-2 band metadata is invalid")
    bands = asset.get("raster:bands", asset.get("bands"))
    if not isinstance(bands, list) or not bands or not isinstance(bands[0], dict):
        raise ProviderContractChanged("Sentinel-2 band scale or offset is missing")
    try:
        band = bands[0]
        raw_scale = band.get("scale", band.get("raster:scale"))
        raw_offset = band.get("offset", band.get("raster:offset", 0.0))
        if raw_scale is None or raw_offset is None:
            raise TypeError
        scale = float(raw_scale)
        offset = float(raw_offset)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
        raise ProviderContractChanged("Sentinel-2 band scale or offset is missing") from exc
    if not np.isfinite(scale) or scale <= 0 or not np.isfinite(offset):
        raise ProviderContractChanged("Sentinel-2 band scale or offset is invalid")
    return scale, offset


def _scene_urls(scene: Sentinel2Scene) -> tuple[str, ...]:
    return scene.red_url, scene.green_url, scene.blue_url, scene.scl_url


def _valid_asset_url(url: str) -> bool:
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _ASSET_HOST
        and not parsed.username
        and not parsed.password
        and parsed.path.lower().endswith(".tif")
    )


def _parse_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Sentinel-2 dates must be UTC ISO-8601 values")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("Sentinel-2 dates must be UTC ISO-8601 values") from exc


class BandReader(Protocol):
    georeference: Any
    layout: Any

    @property
    def nodata(self) -> float: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...


ReaderFactory = Callable[[str, Path], BandReader]


class Sentinel2Acquirer:
    """Cache bounded RGB and scene-classification windows from selected L2A scenes."""

    def __init__(
        self,
        catalog: Sentinel2CatalogClient | None = None,
        reader_factory: ReaderFactory | None = None,
    ) -> None:
        self._catalog = catalog or Sentinel2CatalogClient()
        self._reader_factory = reader_factory or _remote_reader

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        if (
            selection.provider_id != PROVIDER_ID
            or selection.product_id != PRODUCT_ID
            or selection.kind is not DatasetKind.IMAGERY
            or request.kind is not DatasetKind.IMAGERY
        ):
            raise ValueError("Sentinel-2 acquirer received an incompatible selection")
        start, end, cloud = _temporal_policy(selection.temporal_policy)
        scenes = discover_sentinel2_scenes(roi, selection.temporal_policy, self._catalog)
        key = hashlib.sha256(
            f"window-v3|{roi.west},{roi.south},{roi.east},{roi.north}|{start}|{end}|{cloud}".encode()
        ).hexdigest()[:20]
        target = cache_directory / PROVIDER_ID / PRODUCT_ID / key
        paths: list[Path] = []
        cached_count = 0
        for completed, scene in enumerate(scenes, start=1):
            if cancellation_requested():
                raise JobCancelled("Sentinel-2 acquisition was cancelled")
            path = target / f"{_safe_scene_id(scene.id)}.npy"
            if imagery_window_is_valid(path):
                cached_count += 1
                cached = True
            else:
                scene_roi = _intersection(scene.bounds, roi)
                if scene_roi is None:
                    continue
                data, bounds, nodata = self._read_scene(
                    scene, scene_roi, target / "ranges" / _safe_scene_id(scene.id)
                )
                if data.shape[0] * data.shape[1] > _MAXIMUM_WINDOW_PIXELS:
                    raise RasterFormatError("Sentinel-2 source window exceeds the pixel limit")
                write_imagery_window(
                    path, data, bounds, nodata, ("B02", "B03", "B04", "SCL")
                )
                cached = False
            paths.append(path)
            _report(progress_callback, completed, len(scenes), path, cached)
        manifest = target / "source_manifest.json"
        _write_scene_manifest(manifest, selection.temporal_policy, scenes)
        return AcquiredRasterLayer(
            PROVIDER_ID,
            PRODUCT_ID,
            DatasetKind.IMAGERY,
            tuple(paths),
            cached_count,
            (manifest,),
        )

    def _read_scene(
        self, scene: Sentinel2Scene, roi: BBoxWGS84, range_directory: Path
    ) -> tuple[NDArray[np.float32], ProjectedBounds, float]:
        from pyproj import Transformer

        transformer = Transformer.from_crs(4326, scene.epsg, always_xy=True)
        west, south, east, north = transformer.transform_bounds(
            roi.west, roi.south, roi.east, roi.north, densify_pts=21
        )
        requested = ProjectedBounds(west, south, east, north, scene.epsg)
        readers = {
            "B04": self._reader_factory(scene.red_url, range_directory / "red"),
            "B03": self._reader_factory(scene.green_url, range_directory / "green"),
            "B02": self._reader_factory(scene.blue_url, range_directory / "blue"),
            "SCL": self._reader_factory(scene.scl_url, range_directory / "scl"),
        }
        red, exact = readers["B04"].read_bounds(requested)
        green, green_bounds = readers["B03"].read_bounds(exact)
        blue, blue_bounds = readers["B02"].read_bounds(exact)
        if red.ndim != 2 or green.shape != red.shape or blue.shape != red.shape:
            raise RasterFormatError("Sentinel-2 RGB bands are not aligned")
        if green_bounds != exact or blue_bounds != exact:
            raise RasterFormatError("Sentinel-2 RGB bounds are not aligned")
        scl, scl_bounds = readers["SCL"].read_bounds(exact)
        if scl.ndim != 2:
            raise RasterFormatError("Sentinel-2 SCL band must contain one channel")
        classification = _resample_nearest(
            scl, scl_bounds, exact, (red.shape[0], red.shape[1])
        )
        invalid = (
            (red == readers["B04"].nodata)
            | (green == readers["B03"].nodata)
            | (blue == readers["B02"].nodata)
        )
        red = red * scene.red_scale + scene.red_offset
        green = green * scene.green_scale + scene.green_offset
        blue = blue * scene.blue_scale + scene.blue_offset
        nodata = -9999.0
        red[invalid] = nodata
        green[invalid] = nodata
        blue[invalid] = nodata
        return (
            np.stack((blue, green, red, classification), axis=2).astype(np.float32),
            exact,
            nodata,
        )


def _remote_reader(url: str, cache_directory: Path) -> BigTiffFloatTileReader:
    return open_float_tile_reader(
        HttpRangeReader(
            url,
            cache_directory,
            allowed_hosts=frozenset({_ASSET_HOST}),
            maximum_source_bytes=_MAXIMUM_SOURCE_BYTES,
        )
    )


def _write_scene_manifest(
    path: Path, temporal_policy: str | None, scenes: tuple[Sentinel2Scene, ...]
) -> None:
    payload = {
        "provider_id": PROVIDER_ID,
        "product_id": PRODUCT_ID,
        "temporal_policy": temporal_policy,
        "scenes": [
            {
                "id": scene.id,
                "grid_code": scene.grid_code,
                "acquired_at": scene.acquired_at,
                "cloud_cover_percent": scene.cloud_cover_percent,
                "epsg": scene.epsg,
                "rgb_scale": [scene.red_scale, scene.green_scale, scene.blue_scale],
                "rgb_offset": [scene.red_offset, scene.green_offset, scene.blue_offset],
                "bounds_wgs84": [
                    scene.bounds.west,
                    scene.bounds.south,
                    scene.bounds.east,
                    scene.bounds.north,
                ],
            }
            for scene in scenes
        ],
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if path.is_file():
        if path.read_bytes() != encoded:
            raise RasterFormatError("Cached Sentinel-2 provenance does not match its scenes")
        return
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


def _intersection(left: BBoxWGS84, right: BBoxWGS84) -> BBoxWGS84 | None:
    west = max(left.west, right.west)
    south = max(left.south, right.south)
    east = min(left.east, right.east)
    north = min(left.north, right.north)
    return None if east <= west or north <= south else BBoxWGS84(west, south, east, north)


def _rectangles_cover(roi: BBoxWGS84, rectangles: tuple[BBoxWGS84, ...]) -> bool:
    clipped = tuple(
        intersection
        for rectangle in rectangles
        if (intersection := _intersection(roi, rectangle)) is not None
    )
    x_edges = sorted(
        {roi.west, roi.east, *(value for item in clipped for value in (item.west, item.east))}
    )
    for west, east in pairwise(x_edges):
        if east <= west:
            continue
        midpoint = (west + east) / 2.0
        intervals = sorted(
            (item.south, item.north)
            for item in clipped
            if item.west <= midpoint <= item.east
        )
        covered_to = roi.south
        for south, north in intervals:
            if south > covered_to:
                return False
            covered_to = max(covered_to, north)
            if covered_to >= roi.north:
                break
        if covered_to < roi.north:
            return False
    return True


def _resample_nearest(
    source: NDArray[np.float32],
    source_bounds: ProjectedBounds,
    target_bounds: ProjectedBounds,
    shape: tuple[int, int],
) -> NDArray[np.float32]:
    height, width = shape
    target_width = target_bounds.east - target_bounds.west
    target_height = target_bounds.north - target_bounds.south
    source_width = source_bounds.east - source_bounds.west
    source_height = source_bounds.north - source_bounds.south
    x = target_bounds.west + (np.arange(width) + 0.5) * target_width / width
    y = target_bounds.north - (np.arange(height) + 0.5) * target_height / height
    columns = np.floor((x - source_bounds.west) * source.shape[1] / source_width).astype(int)
    rows = np.floor((source_bounds.north - y) * source.shape[0] / source_height).astype(int)
    rows = np.clip(rows, 0, source.shape[0] - 1)
    columns = np.clip(columns, 0, source.shape[1] - 1)
    return np.asarray(source[rows[:, None], columns], dtype=np.float32)


def _temporal_policy(value: str | None) -> tuple[str, str, float]:
    try:
        period, cloud_text = (value or "").split(";cloud=")
        start, end = period.split("/")
        cloud = float(cloud_text)
        _parse_datetime(start)
        _parse_datetime(end)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Sentinel-2 temporal policy must be START/END;cloud=PERCENT"
        ) from exc
    if not 0 <= cloud <= 100:
        raise ValueError("Sentinel-2 cloud limit must be between 0 and 100")
    return start, end, cloud


def _safe_scene_id(value: str) -> str:
    safe_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
    if not value or any(character not in safe_characters for character in value):
        raise ProviderContractChanged("Sentinel-2 scene identifier is unsafe")
    return value


def _report(
    callback: Callable[[TransferProgress], None] | None,
    completed: int,
    total: int,
    path: Path,
    cached: bool,
) -> None:
    if callback is not None:
        size = path.stat().st_size if path.is_file() else 0
        callback(
            TransferProgress(
                DatasetKind.IMAGERY.value,
                completed - 1,
                total,
                path.name,
                size,
                size if cached else None,
                cached,
            )
        )
