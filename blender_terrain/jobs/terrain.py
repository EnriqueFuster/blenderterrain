"""Prepare elevation and bathymetry selected by a confirmed acquisition plan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..catalog.models import Catalog, DatasetKind
from ..catalog.selection import (
    AcquisitionPlan,
    AcquisitionRequest,
    LayerRequest,
    SelectionBundle,
)
from ..core import (
    BBoxWGS84,
    ImportPlan,
    ProcessedBathymetryTile,
    ProcessedElevationTile,
    RegionOfInterest,
    TransferProgress,
    create_import_plan,
    geographic_source_bounds,
    process_elevation_tiles,
    process_gebco_tiles,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer, acquire_plan_layers
from ..errors import JobCancelled, NoCoverageError, RasterFormatError, UserInputError
from ..providers.registry import build_raster_acquirers


class ElevationProcessor(Protocol):
    """Callable contract for provider-neutral elevation processing."""

    def __call__(
        self,
        source_paths: tuple[Path, ...],
        plan: ImportPlan,
        progress_callback: Callable[[int, int], None] | None = None,
        maximum_source_window_pixels: int = 4_194_304,
        region: RegionOfInterest | None = None,
    ) -> tuple[ProcessedElevationTile, ...]: ...


@dataclass(frozen=True, slots=True)
class PreparedElevation:
    """Acquired sources and processed terrain from one confirmed elevation selection."""

    acquired: AcquiredRasterLayer | None
    import_plan: ImportPlan
    tiles: tuple[ProcessedElevationTile, ...]


@dataclass(frozen=True, slots=True)
class PreparedBathymetry:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedBathymetryTile, ...]


def acquire_confirmed_sources(
    plan: AcquisitionPlan,
    cache_directory: Path,
    progress_callback: Callable[[TransferProgress], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    source_roi: BBoxWGS84 | None = None,
) -> tuple[AcquiredRasterLayer, ...]:
    """Execute the exact per-layer providers confirmed before worker startup."""

    provider_ids = tuple(selection.provider_id for selection in plan.selections.selections)
    return acquire_plan_layers(
        plan,
        acquirer_factory(provider_ids),
        cache_directory,
        progress_callback,
        cancellation_requested,
        source_roi,
    )


def prepare_confirmed_elevation(
    plan: AcquisitionPlan,
    catalog: Catalog,
    cache_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    maximum_elevation_samples: int = 16_777_216,
    maximum_imagery_pixels: int = 67_108_864,
    region: RegionOfInterest | None = None,
    manual_tile_rows: int | None = None,
    manual_tile_columns: int | None = None,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> PreparedElevation:
    """Acquire and process the single elevation layer confirmed for a terrain."""

    elevation_selections = tuple(
        selection
        for selection in plan.selections.selections
        if selection.kind in {DatasetKind.DTM, DatasetKind.DSM}
    )
    if len(elevation_selections) != 1:
        raise UserInputError("Terrain creation requires exactly one DTM or DSM selection")
    selection = elevation_selections[0]
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not selection.kind
    ):
        raise UserInputError("Selected elevation product does not match the catalog")
    layer_request = plan.request.layer(selection.kind)
    imagery_selection = plan.selections.for_kind(DatasetKind.IMAGERY)
    imagery_request = None if imagery_selection is None else plan.request.layer(DatasetKind.IMAGERY)
    import_plan = create_import_plan(
        plan.request.roi,
        product.id,
        layer_request.target_resolution_m,
        imagery_selection is not None,
        None if imagery_request is None else imagery_request.target_resolution_m,
        manual_tile_rows,
        manual_tile_columns,
        maximum_elevation_samples=maximum_elevation_samples,
        maximum_imagery_pixels=maximum_imagery_pixels,
        native_resolution_override=product.capabilities.native_resolution_m,
        use_global_utm=product.jurisdiction == "global",
        working_crs_epsg=None if product.wms is None else product.wms.crs_epsg,
    )
    resolved_request = LayerRequest(
        layer_request.kind,
        import_plan.elevation_resolution_metres,
        layer_request.temporal_policy,
    )
    elevation_plan = AcquisitionPlan(
        AcquisitionRequest(
            plan.request.roi,
            (resolved_request,),
            plan.request.license_profile,
        ),
        SelectionBundle((selection,)),
    )
    source_roi = geographic_source_bounds(import_plan) if product.jurisdiction == "global" else None
    try:
        acquired = acquire_confirmed_sources(
            elevation_plan,
            cache_directory,
            transfer_callback,
            cancellation_requested,
            acquirer_factory,
            source_roi,
        )[0]
    except NoCoverageError:
        if plan.selections.for_kind(DatasetKind.BATHYMETRY) is None:
            raise
        return PreparedElevation(None, import_plan, _empty_elevation_tiles(import_plan))

    def report_processing(completed: int, total: int) -> None:
        if cancellation_requested():
            raise JobCancelled("Elevation processing was cancelled")
        if processing_callback is not None:
            processing_callback(completed, total)

    if cancellation_requested():
        raise JobCancelled("Elevation processing was cancelled")
    tiles = elevation_processor(
        acquired.paths,
        import_plan,
        report_processing,
        region=region,
    )
    return PreparedElevation(acquired, import_plan, tiles)


def _empty_elevation_tiles(plan: ImportPlan) -> tuple[ProcessedElevationTile, ...]:
    """Create only the target grids needed for confirmed bathymetry output."""

    nodata = -32768.0
    return tuple(
        ProcessedElevationTile(
            zone_index,
            tile,
            np.full((tile.rows + 1, tile.columns + 1), nodata, np.float32),
            nodata,
            0,
            0,
            0.0,
        )
        for zone_index in range(len(plan.grids))
        for tile in plan.tiles_for_grid(zone_index)
    )


def prepare_confirmed_bathymetry(
    plan: AcquisitionPlan,
    catalog: Catalog,
    import_plan: ImportPlan,
    cache_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    region: RegionOfInterest | None = None,
) -> PreparedBathymetry | None:
    """Acquire and align the explicitly confirmed bathymetry selection, if any."""

    selection = plan.selections.for_kind(DatasetKind.BATHYMETRY)
    if selection is None:
        return None
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not DatasetKind.BATHYMETRY
        or product.id != "GEBCO_2026"
    ):
        raise UserInputError("Selected bathymetry product is not supported by this worker")
    request = plan.request.layer(DatasetKind.BATHYMETRY)
    bathymetry_plan = AcquisitionPlan(
        AcquisitionRequest(plan.request.roi, (request,), plan.request.license_profile),
        SelectionBundle((selection,)),
    )
    acquired = acquire_confirmed_sources(
        bathymetry_plan,
        cache_directory,
        transfer_callback,
        cancellation_requested,
        acquirer_factory,
        geographic_source_bounds(import_plan),
    )[0]
    tid_path = next((path for path in acquired.auxiliary_paths if path.name == "tid.npy"), None)
    if tid_path is None or len(acquired.paths) != 1:
        raise RasterFormatError("GEBCO elevation and TID windows are required")
    tiles = process_gebco_tiles(
        acquired.paths[0],
        tid_path,
        import_plan,
        processing_callback,
        region,
    )
    return PreparedBathymetry(acquired, tiles)
