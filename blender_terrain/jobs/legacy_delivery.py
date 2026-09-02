"""Dependencies used by the pre-catalog Spanish delivery job."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..core import ImportPlan, ProcessedElevationTile, RegionOfInterest
from ..models import CatalogItem


class LocalElevationClient:
    """Expose user-provided files through the legacy downloader contract."""

    def __init__(self, paths: tuple[Path, ...]) -> None:
        self._paths = {path.name: path for path in paths}

    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path:
        path = self._paths[item.filename]
        size = path.stat().st_size
        if progress_callback is not None:
            progress_callback(size, size)
        return path


class ElevationProcessor(Protocol):
    """Callable contract for the legacy elevation processing stage."""

    def __call__(
        self,
        source_paths: tuple[Path, ...],
        plan: ImportPlan,
        progress_callback: Callable[[int, int], None] | None = None,
        maximum_source_window_pixels: int = 4_194_304,
        region: RegionOfInterest | None = None,
    ) -> tuple[ProcessedElevationTile, ...]: ...
