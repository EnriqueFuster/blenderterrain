"""Provider-neutral execution of confirmed raster selections."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..catalog.models import DatasetKind
from ..catalog.selection import AcquisitionPlan, LayerRequest, ProductSelection
from ..errors import JobCancelled, ProviderUnavailableError
from .delivery import TransferProgress
from .roi import BBoxWGS84


@dataclass(frozen=True, slots=True)
class AcquiredRasterLayer:
    """Local raster files produced by one explicitly selected product."""

    provider_id: str
    product_id: str
    kind: DatasetKind
    paths: tuple[Path, ...]
    cached_count: int = 0

    def __post_init__(self) -> None:
        if not self.paths:
            raise ValueError("An acquired raster layer must contain at least one file")
        if not 0 <= self.cached_count <= len(self.paths):
            raise ValueError("Cached file count is outside the acquired path count")


class RasterAcquirer(Protocol):
    """Capability implemented by each provider-specific acquisition adapter."""

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer: ...


def acquire_plan_layers(
    plan: AcquisitionPlan,
    acquirers: Mapping[str, RasterAcquirer],
    cache_directory: Path,
    progress_callback: Callable[[TransferProgress], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
) -> tuple[AcquiredRasterLayer, ...]:
    """Execute only the providers locked into an immutable acquisition plan."""

    acquired: list[AcquiredRasterLayer] = []
    for selection in plan.selections.selections:
        if cancellation_requested():
            raise JobCancelled("Raster acquisition was cancelled")
        acquirer = acquirers.get(selection.provider_id)
        if acquirer is None:
            raise ProviderUnavailableError(
                f"No acquisition adapter is registered for {selection.provider_id}"
            )
        result = acquirer.acquire(
            selection,
            plan.request.layer(selection.kind),
            plan.request.roi,
            cache_directory,
            progress_callback,
            cancellation_requested,
        )
        if (
            result.provider_id != selection.provider_id
            or result.product_id != selection.product_id
            or result.kind is not selection.kind
        ):
            raise ProviderUnavailableError(
                "Acquisition adapter returned data for a different selection"
            )
        acquired.append(result)
    return tuple(acquired)
