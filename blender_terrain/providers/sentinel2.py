"""Discovery of open Sentinel-2 L2A scenes through Earth Search STAC."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from ..core.roi import BBoxWGS84
from ..errors import ProviderContractChanged, ProviderUnavailableError

PRODUCT_ID = "SENTINEL2_L2A"
PROVIDER_ID = "sentinel2"
STAC_SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION_ID = "sentinel-2-c1-l2a"
_ASSET_HOST = "e84-earth-search-sentinel-data.s3.us-west-2.amazonaws.com"
_MAXIMUM_RESPONSE_BYTES = 5 * 1024 * 1024


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
        sorted(scenes, key=lambda scene: (scene.cloud_cover_percent, scene.acquired_at, scene.id))
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
