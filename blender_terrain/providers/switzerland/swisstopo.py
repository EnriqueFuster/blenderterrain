"""Bounded STAC discovery for official swisstopo raster products."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

from ...core.roi import BBoxWGS84, RegionOfInterest
from ...errors import ProviderContractChanged, ProviderUnavailableError

STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
SWISSALTI3D_COLLECTION = "ch.swisstopo.swissalti3d"
SWISSSURFACE3D_COLLECTION = "ch.swisstopo.swisssurface3d-raster"
SWISSIMAGE_COLLECTION = "ch.swisstopo.swissimage-dop10"
SWISSTOPO_COLLECTIONS = frozenset(
    {SWISSALTI3D_COLLECTION, SWISSSURFACE3D_COLLECTION, SWISSIMAGE_COLLECTION}
)
_ASSET_HOST = "data.geo.admin.ch"
_PAGE_LIMIT = 100
_MAXIMUM_PAGES = 20
_MAXIMUM_ITEMS = 2_000
_MAXIMUM_RESPONSE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SwisstopoAsset:
    """One validated GeoTIFF asset from a swisstopo STAC item."""

    key: str
    url: str
    media_type: str
    gsd: float
    epsg: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SwisstopoItem:
    """One dated 1 km source tile and its supported COG variants."""

    id: str
    collection: str
    acquired_at: str
    bounds: BBoxWGS84
    geometry: RegionOfInterest
    assets: tuple[SwisstopoAsset, ...]


@dataclass(frozen=True, slots=True)
class SwisstopoPage:
    """One parsed STAC FeatureCollection page."""

    items: tuple[SwisstopoItem, ...]
    next_url: str | None


class _Response(Protocol):
    def read(self, amount: int = -1) -> bytes: ...
    def __enter__(self) -> _Response: ...
    def __exit__(self, *args: object) -> None: ...


class _Opener(Protocol):
    def open(self, request: Request, timeout: float) -> _Response: ...


def swisstopo_items_url(collection: str, roi: BBoxWGS84) -> str:
    """Build the first bounded STAC items request for a supported collection."""

    _validate_collection(collection)
    query = urlencode(
        {
            "bbox": f"{roi.west:g},{roi.south:g},{roi.east:g},{roi.north:g}",
            "limit": str(_PAGE_LIMIT),
        }
    )
    return f"{STAC_BASE_URL}/collections/{collection}/items?{query}"


def parse_swisstopo_page(
    payload: bytes,
    collection: str,
    supported_resolutions: frozenset[float],
) -> SwisstopoPage:
    """Validate the STAC fields required for swisstopo raster acquisition."""

    _validate_collection(collection)
    if not supported_resolutions or any(value <= 0 for value in supported_resolutions):
        raise ValueError("Supported swisstopo resolutions must be positive")
    try:
        document = json.loads(payload)
        features = document["features"]
        links = document.get("links", [])
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ProviderContractChanged("swisstopo STAC response schema changed") from exc
    if document.get("type") != "FeatureCollection" or not isinstance(features, list):
        raise ProviderContractChanged("swisstopo STAC response is not a FeatureCollection")
    if not isinstance(links, list):
        raise ProviderContractChanged("swisstopo STAC response has no valid links")
    items = tuple(
        _parse_item(feature, collection, supported_resolutions) for feature in features
    )
    identifiers = [item.id for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ProviderContractChanged("swisstopo STAC page contains duplicate items")
    return SwisstopoPage(items, _next_link(links, collection))


class SwisstopoCatalogClient:
    """Follow bounded STAC item pagination for any configured swisstopo product."""

    def __init__(self, opener: _Opener | None = None) -> None:
        self._opener = opener or build_opener()

    def search(
        self,
        collection: str,
        roi: BBoxWGS84,
        supported_resolutions: frozenset[float],
    ) -> tuple[SwisstopoItem, ...]:
        url: str | None = swisstopo_items_url(collection, roi)
        items: dict[str, SwisstopoItem] = {}
        page_count = 0
        while url is not None:
            page_count += 1
            if page_count > _MAXIMUM_PAGES:
                raise ProviderContractChanged("swisstopo STAC pagination exceeds its limit")
            request = Request(
                url,
                headers={
                    "User-Agent": "BlenderTerrain/0.5 (+https://github.com/EnriqueFuster/blenderterrain)",
                    "Accept": "application/geo+json,application/json",
                },
            )
            try:
                with self._opener.open(request, timeout=30.0) as response:
                    payload = response.read(_MAXIMUM_RESPONSE_BYTES + 1)
            except HTTPError as exc:
                raise ProviderUnavailableError(
                    f"swisstopo STAC returned HTTP {exc.code}"
                ) from None
            except (URLError, TimeoutError, OSError) as exc:
                raise ProviderUnavailableError("swisstopo STAC request failed") from exc
            if len(payload) > _MAXIMUM_RESPONSE_BYTES:
                raise ProviderContractChanged("swisstopo STAC response exceeds its size limit")
            page = parse_swisstopo_page(payload, collection, supported_resolutions)
            for item in page.items:
                items.setdefault(item.id, item)
            if len(items) > _MAXIMUM_ITEMS:
                raise ProviderContractChanged("swisstopo STAC item count exceeds its limit")
            url = page.next_url
        return tuple(items.values())


def _parse_item(
    feature: Any,
    collection: str,
    supported_resolutions: frozenset[float],
) -> SwisstopoItem:
    try:
        if feature["type"] != "Feature" or feature["collection"] != collection:
            raise ValueError
        item_id = feature["id"]
        acquired_at = feature["properties"]["datetime"]
        bbox = feature["bbox"]
        geometry = RegionOfInterest.from_geojson_geometry(feature["geometry"])
        raw_assets = feature["assets"]
        bounds = BBoxWGS84(*(float(value) for value in bbox))
        _parse_datetime(acquired_at)
        if not isinstance(item_id, str) or not item_id or not isinstance(raw_assets, dict):
            raise TypeError
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderContractChanged("swisstopo STAC item schema changed") from exc

    assets: list[SwisstopoAsset] = []
    for key, value in raw_assets.items():
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise ProviderContractChanged("swisstopo STAC asset schema changed")
        if "image/tiff" not in value["type"].lower():
            continue
        try:
            gsd = float(value["gsd"])
            epsg = int(value["proj:epsg"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderContractChanged("swisstopo STAC asset schema changed") from exc
        if gsd not in supported_resolutions:
            continue
        assets.append(_parse_asset(key, value, gsd, epsg))
    if not assets:
        raise ProviderContractChanged("swisstopo STAC item has no supported GeoTIFF asset")
    identities = [(asset.key, asset.url) for asset in assets]
    if len(identities) != len(set(identities)):
        raise ProviderContractChanged("swisstopo STAC item has duplicate assets")
    return SwisstopoItem(
        item_id,
        collection,
        acquired_at,
        bounds,
        geometry,
        tuple(sorted(assets, key=lambda asset: (asset.gsd, asset.key))),
    )


def _parse_asset(key: Any, value: dict[str, Any], gsd: float, epsg: int) -> SwisstopoAsset:
    try:
        url = value["href"]
        media_type = value["type"]
        checksum = value["file:checksum"]
    except KeyError as exc:
        raise ProviderContractChanged("swisstopo STAC asset schema changed") from exc
    parsed = urlsplit(url) if isinstance(url, str) else None
    if (
        not isinstance(key, str)
        or not key
        or not isinstance(media_type, str)
        or parsed is None
        or parsed.scheme != "https"
        or parsed.hostname != _ASSET_HOST
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.path.lower().endswith((".tif", ".tiff"))
        or epsg != 2056
        or not math.isfinite(gsd)
    ):
        raise ProviderContractChanged("swisstopo STAC asset values are unsupported")
    sha256 = _parse_sha256_multihash(checksum)
    return SwisstopoAsset(key, url, media_type, gsd, epsg, sha256)


def _parse_sha256_multihash(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 68 or value[:4].lower() != "1220":
        raise ProviderContractChanged("swisstopo STAC checksum is not SHA2-256 multihash")
    digest = value[4:]
    try:
        bytes.fromhex(digest)
    except ValueError as exc:
        raise ProviderContractChanged("swisstopo STAC checksum is invalid") from exc
    return digest.lower()


def _next_link(links: list[Any], collection: str) -> str | None:
    next_urls: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            raise ProviderContractChanged("swisstopo STAC link schema changed")
        if link.get("rel") == "next":
            href = link.get("href")
            if not isinstance(href, str) or not _valid_items_url(href, collection):
                raise ProviderContractChanged("swisstopo STAC next link is not trusted")
            next_urls.append(href)
    if len(next_urls) > 1:
        raise ProviderContractChanged("swisstopo STAC response has multiple next links")
    return next_urls[0] if next_urls else None


def _valid_items_url(url: str, collection: str) -> bool:
    parsed = urlsplit(url)
    expected_path = f"/api/stac/v1/collections/{collection}/items"
    return (
        parsed.scheme == "https"
        and parsed.hostname == _ASSET_HOST
        and not parsed.username
        and not parsed.password
        and parsed.path == expected_path
        and not parsed.fragment
    )


def _validate_collection(collection: str) -> None:
    if collection not in SWISSTOPO_COLLECTIONS:
        raise ValueError("Unsupported swisstopo STAC collection")


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError
    return parsed
