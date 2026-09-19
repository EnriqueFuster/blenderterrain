"""Discovery of open Sentinel-2 L2A scenes through Earth Search STAC."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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
)
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


def _parse_scene(feature: Any) -> Sentinel2Scene:
    try:
        properties = feature["properties"]
        assets = feature["assets"]
        bbox = feature["bbox"]
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
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderContractChanged("Sentinel-2 STAC item schema changed") from exc
    _parse_datetime(scene.acquired_at)
    if (
        not scene.id
        or not 0 <= scene.cloud_cover_percent <= 100
        or not 32601 <= scene.epsg <= 32760
        or any(not _valid_asset_url(url) for url in _scene_urls(scene))
    ):
        raise ProviderContractChanged("Sentinel-2 STAC item values are unsupported")
    return scene


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
        scenes = self._catalog.search(roi, start, end, cloud)
        if not scenes:
            raise NoCoverageError("Sentinel-2 has no scenes matching the temporal policy")
        key = hashlib.sha256(
            f"window-v1|{roi.west},{roi.south},{roi.east},{roi.north}|{start}|{end}|{cloud}".encode()
        ).hexdigest()[:20]
        target = cache_directory / PROVIDER_ID / PRODUCT_ID / key
        paths: list[Path] = []
        cached_count = 0
        # The clearest scene is enough for this first executable increment. Mosaicking
        # multiple footprints is added when dynamic coverage discovery is introduced.
        scene = scenes[0]
        path = target / f"{_safe_scene_id(scene.id)}.npy"
        if imagery_window_is_valid(path):
            paths.append(path)
            cached_count = 1
            _report(progress_callback, 1, 1, path, True)
        else:
            if cancellation_requested():
                raise JobCancelled("Sentinel-2 acquisition was cancelled")
            data, bounds, nodata = self._read_scene(scene, roi, target / "ranges")
            if data.shape[0] * data.shape[1] > _MAXIMUM_WINDOW_PIXELS:
                raise RasterFormatError("Sentinel-2 source window exceeds the pixel limit")
            write_imagery_window(path, data, bounds, nodata, ("B02", "B03", "B04", "SCL"))
            paths.append(path)
            _report(progress_callback, 1, 1, path, False)
        return AcquiredRasterLayer(
            PROVIDER_ID, PRODUCT_ID, DatasetKind.IMAGERY, tuple(paths), cached_count
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
        return (
            np.stack((blue, green, red, classification), axis=2).astype(np.float32),
            exact,
            readers["B04"].nodata,
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
