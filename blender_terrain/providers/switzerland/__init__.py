"""Swiss national geodata providers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...catalog import Catalog, DatasetKind, LayerRequest, ProductSelection
from ...core.acquisition import AcquiredRasterLayer
from ...core.delivery import TransferProgress
from ...core.roi import BBoxWGS84
from .imagery import SwisstopoImageryAcquirer
from .swisstopo import SwisstopoElevationAcquirer


class SwisstopoAcquirer:
    """Dispatch one confirmed swisstopo selection to its raster implementation."""

    def __init__(self, catalog: Catalog | None = None) -> None:
        self._elevation = SwisstopoElevationAcquirer(catalog)
        self._imagery = SwisstopoImageryAcquirer(catalog)

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        implementation = (
            self._imagery
            if selection.kind is DatasetKind.IMAGERY
            else self._elevation
        )
        return implementation.acquire(
            selection,
            request,
            roi,
            cache_directory,
            progress_callback,
            cancellation_requested,
        )

