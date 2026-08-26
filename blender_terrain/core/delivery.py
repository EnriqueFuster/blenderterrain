"""Coordinate bounded elevation and PNOA source delivery into the cache."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..errors import BlenderTerrainError, JobCancelled
from ..io.png_validation import validate_png
from ..io.tiff_validation import validate_tiff_header
from ..models import CatalogItem, ProjectedBounds
from .discovery import DiscoveryResult
from .imagery import ImageryTileRequest, plan_imagery_tiles
from .planning import ImportPlan


@dataclass(frozen=True, slots=True)
class TransferProgress:
    """Byte progress for one file within a multi-file delivery."""

    kind: str
    file_index: int
    file_count: int
    filename: str
    written_bytes: int
    expected_bytes: int | None


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Validated cache paths produced or reused by one import request."""

    elevation_paths: tuple[Path, ...]
    imagery_paths: tuple[Path, ...]
    warnings: tuple[str, ...] = ()


class ElevationDownloader(Protocol):
    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path: ...


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
    discovery: DiscoveryResult,
    cache_directory: Path,
    elevation_client: ElevationDownloader,
    imagery_client: ImageryDownloader,
    progress_callback: Callable[[TransferProgress], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> DeliveryResult:
    """Download or validate every source required by a prepared import plan."""

    elevation_directory = cache_directory / "elevation"
    imagery_requests = plan_imagery_tiles(plan)
    imagery_directory = cache_directory / "imagery" / _imagery_cache_key(imagery_requests)
    elevation_paths: list[Path] = []
    imagery_paths: list[Path] = []

    for index, item in enumerate(discovery.items):
        _check_cancelled(cancellation_requested)
        destination = elevation_directory / item.filename
        if destination.is_file():
            validate_tiff_header(destination)
            elevation_paths.append(destination)
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
    return DeliveryResult(tuple(elevation_paths), tuple(imagery_paths), tuple(warnings))


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
