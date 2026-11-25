"""Bounded STAC discovery for official swisstopo raster products."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer

from ...catalog import (
    Catalog,
    CoverageMatch,
    DatasetKind,
    LayerRequest,
    ProductSelection,
    load_bundled_catalog,
)
from ...core.acquisition import AcquiredRasterLayer
from ...core.delivery import TransferProgress
from ...core.roi import BBoxWGS84, RegionOfInterest
from ...errors import (
    DownloadIntegrityError,
    JobCancelled,
    NoCoverageError,
    ProviderContractChanged,
    ProviderUnavailableError,
    RasterFormatError,
)
from ...io.atomic import finalize_part
from ...io.bigtiff_tiles import BigTiffFloatTileReader, open_float_tile_reader
from ...io.elevation_window import elevation_window_is_valid, write_elevation_window
from ...io.random_access import HttpRangeReader
from ...models import ProjectedBounds

STAC_BASE_URL = "https://data.geo.admin.ch/api/stac/v1"
SWISSALTI3D_COLLECTION = "ch.swisstopo.swissalti3d"
SWISSSURFACE3D_COLLECTION = "ch.swisstopo.swisssurface3d-raster"
SWISSIMAGE_COLLECTION = "ch.swisstopo.swissimage-dop10"
SWISSTOPO_COLLECTIONS = frozenset(
    {SWISSALTI3D_COLLECTION, SWISSSURFACE3D_COLLECTION, SWISSIMAGE_COLLECTION}
)
ASSET_HOST = "data.geo.admin.ch"
_PAGE_LIMIT = 100
_MAXIMUM_PAGES = 20
_MAXIMUM_ITEMS = 2_000
_MAXIMUM_RESPONSE_BYTES = 10 * 1024 * 1024
_MAXIMUM_SOURCE_BYTES = 100_000_000
_ELEVATION_PRODUCTS = {
    "CH_SWISSALTI3D": (SWISSALTI3D_COLLECTION, frozenset({0.5, 2.0})),
    "CH_SWISSSURFACE3D_RASTER": (SWISSSURFACE3D_COLLECTION, frozenset({0.5})),
}


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


class _RasterGrid(Protocol):
    georeference: Any
    layout: Any


class _ElevationReader(_RasterGrid, Protocol):

    @property
    def nodata(self) -> float: ...

    def read_bounds(
        self, bounds: ProjectedBounds
    ) -> tuple[NDArray[np.float32], ProjectedBounds]: ...


ElevationReaderFactory = Callable[[SwisstopoAsset, Path], _ElevationReader]


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


def select_swisstopo_asset(
    item: SwisstopoItem, target_resolution_m: float | None
) -> SwisstopoAsset:
    """Choose the coarsest source that does not undersample the requested grid."""

    ordered = tuple(sorted(item.assets, key=lambda asset: asset.gsd))
    if not ordered:
        raise NoCoverageError("swisstopo item has no supported raster asset")
    if target_resolution_m is None:
        return ordered[0]
    eligible = tuple(asset for asset in ordered if asset.gsd <= target_resolution_m)
    return eligible[-1] if eligible else ordered[0]


def select_latest_swisstopo_items(
    items: tuple[SwisstopoItem, ...],
) -> tuple[SwisstopoItem, ...]:
    """Keep the newest edition when STAC returns one footprint more than once."""

    latest: list[SwisstopoItem] = []
    for item in sorted(
        items,
        key=lambda candidate: (_parse_datetime(candidate.acquired_at), candidate.id),
        reverse=True,
    ):
        if not any(_same_footprint(item.bounds, selected.bounds) for selected in latest):
            latest.append(item)
    return tuple(sorted(latest, key=lambda item: item.id))


def _same_footprint(left: BBoxWGS84, right: BBoxWGS84) -> bool:
    west, south = max(left.west, right.west), max(left.south, right.south)
    east, north = min(left.east, right.east), min(left.north, right.north)
    if west >= east or south >= north:
        return False
    overlap = (east - west) * (north - south)
    smaller = min(
        left.longitude_span * left.latitude_span,
        right.longitude_span * right.latitude_span,
    )
    return overlap / smaller >= 0.99


class SwisstopoElevationAcquirer:
    """Read only intersecting swissALTI3D or swissSURFACE3D COG windows."""

    def __init__(
        self,
        catalog: Catalog | None = None,
        stac: SwisstopoCatalogClient | None = None,
        reader_factory: ElevationReaderFactory | None = None,
    ) -> None:
        self._catalog = catalog or load_bundled_catalog()
        self._stac = stac or SwisstopoCatalogClient()
        self._reader_factory = reader_factory or _remote_elevation_reader

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        product = self._catalog.product(selection.product_id)
        configuration = _ELEVATION_PRODUCTS.get(product.id)
        if (
            configuration is None
            or selection.provider_id != "swisstopo"
            or product.provider_id != selection.provider_id
            or selection.kind not in {DatasetKind.DTM, DatasetKind.DSM}
            or request.kind is not selection.kind
            or product.capabilities.kind is not selection.kind
        ):
            raise ValueError("swisstopo acquirer requires a compatible elevation selection")
        if product.coverage.match(roi) is CoverageMatch.NONE:
            raise NoCoverageError("swisstopo elevation does not intersect the requested ROI")
        collection, resolutions = configuration
        items = select_latest_swisstopo_items(
            self._stac.search(collection, roi, resolutions)
        )
        if not items:
            raise NoCoverageError(f"{product.name} has no STAC items for the ROI")

        key = hashlib.sha256(
            f"elevation-v1|{product.id}|{roi}|{request.target_resolution_m}".encode()
        ).hexdigest()[:20]
        target = cache_directory / selection.provider_id / product.id / key
        projected_roi = project_swiss_roi(roi)
        paths: list[Path] = []
        cached_count = 0
        used: list[tuple[SwisstopoItem, SwisstopoAsset]] = []
        for index, item in enumerate(items):
            if cancellation_requested():
                raise JobCancelled("swisstopo elevation acquisition was cancelled")
            asset = select_swisstopo_asset(item, request.target_resolution_m)
            identity = f"{item.id}|{asset.sha256}"
            path = target / f"{hashlib.sha256(identity.encode()).hexdigest()[:20]}.npy"
            cached = elevation_window_is_valid(path)
            if not cached:
                reader = self._reader_factory(asset, target / "ranges" / path.stem)
                _validate_elevation_reader(reader, asset)
                overlap = intersect_projected_bounds(
                    projected_roi, raster_reader_bounds(reader)
                )
                if overlap is None:
                    continue
                data, exact = reader.read_bounds(overlap)
                if data.dtype != np.float32 or data.ndim != 2 or min(data.shape) < 2:
                    raise RasterFormatError("swisstopo elevation window has an invalid layout")
                path.unlink(missing_ok=True)
                path.with_suffix(".npy.json").unlink(missing_ok=True)
                write_elevation_window(path, data, exact, reader.nodata)
            else:
                cached_count += 1
            paths.append(path)
            used.append((item, asset))
            if progress_callback is not None:
                size = path.stat().st_size
                progress_callback(
                    TransferProgress(
                        selection.kind.value,
                        index,
                        len(items),
                        path.name,
                        size,
                        size,
                        cached=cached,
                    )
                )
        if not paths:
            raise NoCoverageError(f"{product.name} has no raster cells for the ROI")
        manifest = target / "source_manifest.json"
        write_swisstopo_manifest(manifest, product.id, used, vertical_reference="LN02")
        return AcquiredRasterLayer(
            selection.provider_id,
            product.id,
            selection.kind,
            tuple(paths),
            cached_count,
            (manifest,),
        )


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


def project_swiss_roi(roi: BBoxWGS84) -> ProjectedBounds:
    transformer = Transformer.from_crs(4326, 2056, always_xy=True)
    west, south, east, north = transformer.transform_bounds(
        roi.west, roi.south, roi.east, roi.north, densify_pts=21
    )
    return ProjectedBounds(west, south, east, north, 2056)


def raster_reader_bounds(reader: _RasterGrid) -> ProjectedBounds:
    west, south, east, north = reader.georeference.bounds(
        reader.layout.width, reader.layout.height
    )
    return ProjectedBounds(west, south, east, north, reader.georeference.epsg)


def intersect_projected_bounds(
    left: ProjectedBounds, right: ProjectedBounds
) -> ProjectedBounds | None:
    if left.epsg != right.epsg:
        raise RasterFormatError("swisstopo source and requested bounds use different CRS")
    west, south = max(left.west, right.west), max(left.south, right.south)
    east, north = min(left.east, right.east), min(left.north, right.north)
    if west >= east or south >= north:
        return None
    return ProjectedBounds(west, south, east, north, left.epsg)


def _remote_elevation_reader(
    asset: SwisstopoAsset, cache_directory: Path
) -> BigTiffFloatTileReader:
    source = HttpRangeReader(
        asset.url,
        cache_directory,
        allowed_hosts=frozenset({ASSET_HOST}),
        maximum_source_bytes=_MAXIMUM_SOURCE_BYTES,
        cache_key=asset.sha256,
    )
    return open_float_tile_reader(source)


def _validate_elevation_reader(reader: _ElevationReader, asset: SwisstopoAsset) -> None:
    reference = reader.georeference
    if (
        reference.epsg != 2056
        or abs(reference.pixel_width - asset.gsd) > 1e-6
        or abs(reference.pixel_height + asset.gsd) > 1e-6
        or reader.nodata != -9999.0
    ):
        raise DownloadIntegrityError("swisstopo elevation asset grid or NoData changed")


def write_swisstopo_manifest(
    path: Path,
    product_id: str,
    sources: list[tuple[SwisstopoItem, SwisstopoAsset]],
    *,
    vertical_reference: str | None,
) -> None:
    payload = {
        "provider_id": "swisstopo",
        "product_id": product_id,
        "horizontal_crs": "EPSG:2056",
        "sources": [
            {
                "item_id": item.id,
                "acquired_at": item.acquired_at,
                "asset_key": asset.key,
                "gsd": asset.gsd,
                "sha256": asset.sha256,
            }
            for item, asset in sources
        ],
    }
    if vertical_reference is not None:
        payload["vertical_reference"] = vertical_reference
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    if path.is_file() and path.read_bytes() == encoded:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.unlink(missing_ok=True)
    try:
        with part.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            path.unlink()
        finalize_part(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


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
        or parsed.hostname != ASSET_HOST
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
        and parsed.hostname == ASSET_HOST
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
