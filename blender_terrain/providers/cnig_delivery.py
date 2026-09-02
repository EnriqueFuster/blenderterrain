"""Deliver legacy CNIG elevation and optional PNOA imagery into the cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..core.delivery import DeliveryResult, TransferProgress
from ..core.imagery import ImageryTileRequest, plan_imagery_tiles
from ..core.planning import ImportPlan
from ..errors import BlenderTerrainError, JobCancelled
from ..io.png_validation import validate_png
from ..io.tiff_validation import validate_tiff_header
from ..models import CatalogItem, ProjectedBounds


class ElevationDownloader(Protocol):
    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path: ...


class DiscoveredElevationSources(Protocol):
    @property
    def items(self) -> tuple[CatalogItem, ...]: ...


class ImageryDownloader(Protocol):
    def download_png(
        self,
        bounds: ProjectedBounds,
        width: int,
        height: int,
        cache_directory: Path,
        filename: str,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path: ...


def deliver_plan_sources(
    plan: ImportPlan,
    discovery: DiscoveredElevationSources,
    cache_directory: Path,
    elevation_client: ElevationDownloader,
    imagery_client: ImageryDownloader,
    progress_callback: Callable[[TransferProgress], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    local_elevation_paths: tuple[Path, ...] = (),
) -> DeliveryResult:
    """Download or validate sources required by a legacy Spanish import plan."""

    elevation_directory = cache_directory / "elevation"
    imagery_requests = plan_imagery_tiles(plan)
    imagery_directory = cache_directory / "imagery" / _imagery_cache_key(imagery_requests)
    elevation_paths: list[Path] = list(local_elevation_paths)
    imagery_paths: list[Path] = []
    cached_elevation_count = 0
    cached_imagery_count = 0

    for index, item in enumerate(() if local_elevation_paths else discovery.items):
        _check_cancelled(cancellation_requested)
        destination = elevation_directory / item.filename
        if destination.is_file():
            validate_tiff_header(destination)
            elevation_paths.append(destination)
            cached_elevation_count += 1
            _report_cached(
                "elevation", index, len(discovery.items), destination, progress_callback
            )
            continue
        callback = _file_progress(
            "elevation", index, len(discovery.items), item.filename, progress_callback
        )
        elevation_paths.append(
            elevation_client.download_item(item, elevation_directory, progress_callback=callback)
        )

    warnings: list[str] = []
    try:
        for index, request in enumerate(imagery_requests):
            _check_cancelled(cancellation_requested)
            destination = imagery_directory / request.filename
            if destination.is_file():
                validate_png(destination, request.width, request.height)
                imagery_paths.append(destination)
                cached_imagery_count += 1
                _report_cached(
                    "imagery", index, len(imagery_requests), destination, progress_callback
                )
                continue
            callback = _file_progress(
                "imagery", index, len(imagery_requests), request.filename, progress_callback
            )
            imagery_paths.append(
                imagery_client.download_png(
                    request.bounds,
                    request.width,
                    request.height,
                    imagery_directory,
                    request.filename,
                    progress_callback=callback,
                )
            )
    except JobCancelled:
        raise
    except (BlenderTerrainError, ValueError) as exc:
        imagery_paths.clear()
        warnings.append(f"PNOA imagery could not be prepared: {exc}")
    _check_cancelled(cancellation_requested)
    return DeliveryResult(
        tuple(elevation_paths),
        tuple(imagery_paths),
        tuple(warnings),
        cached_elevation_count,
        cached_imagery_count,
    )


def _file_progress(
    kind: str,
    index: int,
    count: int,
    filename: str,
    callback: Callable[[TransferProgress], None] | None,
) -> Callable[[int, int | None], None] | None:
    if callback is None:
        return None

    def report(written_bytes: int, expected_bytes: int | None) -> None:
        callback(TransferProgress(kind, index, count, filename, written_bytes, expected_bytes))

    return report


def _report_cached(
    kind: str,
    index: int,
    count: int,
    path: Path,
    callback: Callable[[TransferProgress], None] | None,
) -> None:
    if callback is None:
        return
    size = path.stat().st_size
    callback(TransferProgress(kind, index, count, path.name, size, size, cached=True))


def _imagery_cache_key(requests: tuple[ImageryTileRequest, ...]) -> str:
    payload = [
        {
            "bounds": [r.bounds.west, r.bounds.south, r.bounds.east, r.bounds.north],
            "epsg": r.bounds.epsg,
            "width": r.width,
            "height": r.height,
            "gsd": r.gsd_metres,
        }
        for r in requests
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _check_cancelled(cancellation_requested: Callable[[], bool]) -> None:
    if cancellation_requested():
        raise JobCancelled("Data delivery was cancelled")
