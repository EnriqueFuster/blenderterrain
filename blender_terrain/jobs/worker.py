"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from ..catalog import load_bundled_catalog
from ..catalog.models import Catalog, DatasetKind
from ..catalog.selection import (
    AcquisitionPlan,
    AcquisitionRequest,
    SelectionBundle,
)
from ..core import (
    BBoxWGS84,
    ImportPlan,
    ProcessedBathymetryTile,
    ProcessedElevationTile,
    ProcessedImageryTile,
    RegionOfInterest,
    TransferProgress,
    create_import_plan,
    deliver_plan_sources,
    discover_sources,
    geographic_source_bounds,
    inspect_local_imagery,
    plan_imagery_tiles,
    process_elevation_tiles,
    process_gebco_tiles,
    process_worldcover_imagery,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer, acquire_plan_layers
from ..core.delivery import ElevationDownloader
from ..core.discovery import CatalogDiscoveryProvider, DiscoveryResult
from ..errors import (
    CatalogContractChanged,
    DownloadAuthorizationRequired,
    DownloadIntegrityError,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    RasterFormatError,
    UserInputError,
)
from ..io.bigtiff_tiles import open_float_tile_reader
from ..io.elevation_output import write_elevation_array, write_quality_array
from ..models import CatalogItem, DatasetProduct
from ..providers.cnig_portal import BASE_URL, CNIGPortalClient
from ..providers.pnoa_wms import PNOA_LAYER, WMS_URL, PNOAWMSClient
from ..providers.registry import build_raster_acquirers
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    is_cancellation_requested,
    read_acquisition_job,
    read_discovery_job,
    write_result,
)

ProviderFactory = Callable[[], CatalogDiscoveryProvider]
ELEVATION_PRODUCTS = tuple(
    product for product in DatasetProduct if product is not DatasetProduct.PNOA_MA
)


@dataclass(frozen=True, slots=True)
class PreparedElevation:
    """Acquired sources and processed terrain from one confirmed elevation selection."""

    acquired: AcquiredRasterLayer
    import_plan: ImportPlan
    tiles: tuple[ProcessedElevationTile, ...]


@dataclass(frozen=True, slots=True)
class PreparedImagery:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedImageryTile, ...]


@dataclass(frozen=True, slots=True)
class PreparedBathymetry:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedBathymetryTile, ...]


def run_confirmed_acquisition_job(
    job_path: Path,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> JobState:
    """Execute a persisted confirmed elevation plan and publish Blender-ready output."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def cancelled() -> bool:
        return is_cancellation_requested(job_path.parent)

    try:
        emit(JobState.VALIDATING, 0.02, "Validating confirmed acquisition")
        job = read_acquisition_job(job_path)
        catalog = load_bundled_catalog()

        def transfer(progress: TransferProgress) -> None:
            fraction = (
                progress.written_bytes / progress.expected_bytes
                if progress.expected_bytes
                else 0.0
            )
            emit(
                JobState.DOWNLOADING_ELEVATION,
                0.05 + 0.65 * min(1.0, fraction),
                _transfer_message(progress),
            )

        def processing(completed: int, total: int) -> None:
            emit(
                JobState.PROCESSING_ELEVATION,
                0.72 + 0.22 * completed / max(1, total),
                f"Processing terrain tile {completed}/{total}",
            )

        prepared = prepare_confirmed_elevation(
            job.plan,
            catalog,
            job_path.parents[2],
            transfer_callback=transfer,
            processing_callback=processing,
            cancellation_requested=cancelled,
            maximum_elevation_samples=job.maximum_elevation_samples,
            maximum_imagery_pixels=job.maximum_imagery_pixels,
            region=job.region,
            manual_tile_rows=job.manual_tile_rows,
            manual_tile_columns=job.manual_tile_columns,
            acquirer_factory=acquirer_factory,
            elevation_processor=elevation_processor,
        )
        processed_directory = job_path.parents[2] / "processed" / job.task_id
        bathymetry = prepare_confirmed_bathymetry(
            job.plan,
            catalog,
            prepared.import_plan,
            job_path.parents[2],
            transfer_callback=transfer,
            processing_callback=lambda completed, total: emit(
                JobState.PROCESSING_ELEVATION,
                0.85 + 0.07 * completed / max(1, total),
                f"Processing bathymetry tile {completed}/{total}",
            ),
            cancellation_requested=cancelled,
            acquirer_factory=acquirer_factory,
        )
        imagery = prepare_confirmed_imagery(
            job.plan,
            catalog,
            prepared.import_plan,
            job_path.parents[2],
            processed_directory / "imagery",
            transfer_callback=transfer,
            processing_callback=lambda completed, total: emit(
                JobState.PROCESSING_ELEVATION,
                0.9 + 0.07 * completed / max(1, total),
                f"Processing texture tile {completed}/{total}",
            ),
            cancellation_requested=cancelled,
            acquirer_factory=acquirer_factory,
        )
        processed_payload = _write_processed_tiles(
            prepared.tiles,
            processed_directory,
            cancelled,
        )
        bathymetry_payload = _write_processed_bathymetry(
            () if bathymetry is None else bathymetry.tiles,
            processed_directory / "bathymetry",
            cancelled,
        )
        product = catalog.product(prepared.acquired.product_id)
        imagery_product = (
            None if imagery is None else catalog.product(imagery.acquired.product_id)
        )
        bathymetry_product = (
            None if bathymetry is None else catalog.product(bathymetry.acquired.product_id)
        )
        source_layers = (
            [prepared.acquired]
            + ([] if bathymetry is None else [bathymetry.acquired])
            + ([] if imagery is None else [imagery.acquired])
        )
        write_result(
            result_path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": job.task_id,
                "import_id": job.import_id,
                "state": JobState.COMPLETE.value,
                "warnings": list(product.coverage.limitations)
                + (
                    []
                    if bathymetry_product is None
                    else list(bathymetry_product.coverage.limitations)
                )
                + ([] if imagery_product is None else list(imagery_product.coverage.limitations)),
                "request": {
                    "bounds_wgs84": asdict(job.plan.request.roi),
                    "roi_geometry_wgs84": (
                        None
                        if job.region is None
                        else job.region.to_geojson_geometry()
                    ),
                    "product": product.id,
                    "elevation_resolution_metres": (
                        prepared.import_plan.elevation_resolution_metres
                    ),
                    "use_imagery": imagery is not None,
                    "use_bathymetry": bathymetry is not None,
                    "imagery_gsd_metres": (
                        None
                        if prepared.import_plan.imagery is None
                        else prepared.import_plan.imagery.gsd_metres
                    ),
                    "manual_tile_rows": job.manual_tile_rows,
                    "manual_tile_columns": job.manual_tile_columns,
                },
                "crs": [asdict(area.crs) for area in prepared.import_plan.work_areas],
                "sources": [
                    {
                        "provider_id": layer.provider_id,
                        "product_id": layer.product_id,
                        "kind": layer.kind.value,
                        "license": catalog.product(layer.product_id).license.identifier,
                        "attribution": (
                            catalog.product(layer.product_id).license.attribution_text
                        ),
                        "paths": [str(path) for path in layer.paths],
                        "auxiliary_paths": [
                            str(path) for path in layer.auxiliary_paths
                        ],
                    }
                    for layer in source_layers
                ],
                "provenance": {
                    "source": product.name,
                    "data_policy_url": product.license.identifier,
                    "license": product.license.identifier,
                    "attribution": product.license.attribution_text,
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                    "uncertainty_summary": _uncertainty_summary(prepared.acquired),
                },
                "elevation_paths": [str(path) for path in prepared.acquired.paths],
                "imagery_paths": (
                    [] if imagery is None else [str(tile.path) for tile in imagery.tiles]
                ),
                "imagery": (
                    []
                    if imagery is None
                    else [
                        {
                            "path": str(tile.path),
                            "bounds": asdict(tile.bounds),
                            "width": tile.width,
                            "height": tile.height,
                            "gsd_metres": tile.gsd_metres,
                        }
                        for tile in imagery.tiles
                    ]
                ),
                "processed_elevation": processed_payload,
                "bathymetry": bathymetry_payload,
                "cache_reuse": {
                    "elevation_files": prepared.acquired.cached_count,
                    "imagery_files": 0 if imagery is None else imagery.acquired.cached_count,
                    "bathymetry_files": (
                        0 if bathymetry is None else bathymetry.acquired.cached_count
                    ),
                },
            },
        )
        emit(
            JobState.COMPLETE,
            1.0,
            f"Prepared {len(prepared.tiles)} terrain and "
            f"{len(bathymetry_payload)} bathymetry and "
            f"{0 if imagery is None else len(imagery.tiles)} texture tile(s)",
        )
        return JobState.COMPLETE
    except JobCancelled as exc:
        return _finish_error(result_path, emit, JobState.CANCELLED, str(exc))
    except ProviderUnavailableError as exc:
        return _finish_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (
        DownloadIntegrityError,
        JobFormatError,
        RasterFormatError,
        UserInputError,
        ValueError,
    ) as exc:
        return _finish_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def _uncertainty_summary(acquired: AcquiredRasterLayer) -> dict[str, object] | None:
    path = next(
        (path for path in acquired.auxiliary_paths if path.name == "uncertainty.json"),
        None,
    )
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


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

    provider_ids = tuple(
        selection.provider_id for selection in plan.selections.selections
    )
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
    imagery_request = (
        None if imagery_selection is None else plan.request.layer(DatasetKind.IMAGERY)
    )
    elevation_plan = AcquisitionPlan(
        AcquisitionRequest(
            plan.request.roi,
            (layer_request,),
            plan.request.license_profile,
        ),
        SelectionBundle((selection,)),
    )
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
    )
    source_roi = (
        geographic_source_bounds(import_plan)
        if product.jurisdiction == "global"
        else None
    )
    acquired = acquire_confirmed_sources(
        elevation_plan,
        cache_directory,
        transfer_callback,
        cancellation_requested,
        acquirer_factory,
        source_roi,
    )[0]
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


def prepare_confirmed_imagery(
    plan: AcquisitionPlan,
    catalog: Catalog,
    import_plan: ImportPlan,
    cache_directory: Path,
    output_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
) -> PreparedImagery | None:
    """Acquire and project the explicitly confirmed imagery selection, if any."""

    selection = plan.selections.for_kind(DatasetKind.IMAGERY)
    if selection is None:
        return None
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not DatasetKind.IMAGERY
        or product.id != "ESA_WORLDCOVER_S2_2021"
    ):
        raise UserInputError("Selected imagery product is not supported by this worker")
    request = plan.request.layer(DatasetKind.IMAGERY)
    imagery_plan = AcquisitionPlan(
        AcquisitionRequest(plan.request.roi, (request,), plan.request.license_profile),
        SelectionBundle((selection,)),
    )
    acquired = acquire_confirmed_sources(
        imagery_plan,
        cache_directory,
        transfer_callback,
        cancellation_requested,
        acquirer_factory,
        geographic_source_bounds(import_plan),
    )[0]
    tiles = process_worldcover_imagery(
        acquired.paths,
        import_plan,
        output_directory,
        processing_callback,
        cancellation_requested,
    )
    return PreparedImagery(acquired, tiles)


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
        acquired.paths[0], tid_path, import_plan, processing_callback
    )
    return PreparedBathymetry(acquired, tiles)


class _LocalElevationClient:
    """Expose already-local sources through the delivery downloader contract."""

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
    """Callable contract for the replaceable elevation processing stage."""

    def __call__(
        self,
        source_paths: tuple[Path, ...],
        plan: ImportPlan,
        progress_callback: Callable[[int, int], None] | None = None,
        maximum_source_window_pixels: int = 4_194_304,
        region: RegionOfInterest | None = None,
    ) -> tuple[ProcessedElevationTile, ...]: ...


def run_discovery_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Run discovery and persist a terminal result for every expected failure."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(
            events_path,
            ProgressEvent(sequence, state, progress, message),
        )
        sequence += 1

    def check_cancelled() -> None:
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Source discovery was cancelled")

    try:
        emit(JobState.VALIDATING, 0.05, "Validating discovery job")
        check_cancelled()
        job = read_discovery_job(job_path)
        plan = _create_plan(job)
        local_imagery = (
            inspect_local_imagery(Path(job.local_imagery_path))
            if job.local_imagery_path is not None
            else None
        )
        if local_imagery is not None and (
            local_imagery.bounds != job.local_imagery_bounds
            or local_imagery.width != job.local_imagery_width
            or local_imagery.height != job.local_imagery_height
        ):
            raise JobFormatError("Local imagery changed after the job was created")
        check_cancelled()
        if job.local_elevation_paths:
            emit(JobState.DISCOVERING, 0.25, "Validating local elevation sources")
            discovery = _local_discovery(job)
        else:
            emit(JobState.DISCOVERING, 0.25, "Discovering CNIG elevation sources")
            discovery = discover_sources(plan, provider_factory())
        check_cancelled()
        payload = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "task_id": job.task_id,
            "import_id": job.import_id,
            "state": JobState.COMPLETE.value,
            "advertised_items": discovery.advertised_items,
            "ignored_items": discovery.ignored_items,
            "estimated_download_mb": discovery.estimated_download_mb,
            "items": [asdict(item) for item in discovery.items],
        }
        write_result(result_path, payload)
        emit(JobState.COMPLETE, 1.0, f"Found {len(discovery.items)} source file(s)")
        return JobState.COMPLETE
    except JobCancelled as exc:
        return _finish_error(result_path, emit, JobState.CANCELLED, str(exc))
    except NoCoverageError as exc:
        return _finish_error(result_path, emit, JobState.NO_COVERAGE, str(exc))
    except CatalogContractChanged as exc:
        return _finish_error(result_path, emit, JobState.PROVIDER_CHANGED, str(exc))
    except ProviderUnavailableError as exc:
        return _finish_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (JobFormatError, RasterFormatError, UserInputError) as exc:
        return _finish_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def run_availability_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Check every elevation product for one ROI without downloading data."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def check_cancelled() -> None:
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Product availability check was cancelled")

    try:
        emit(JobState.VALIDATING, 0.02, "Validating product availability request")
        job = read_discovery_job(job_path)
        provider = provider_factory()
        availability: list[dict[str, object]] = []
        warnings: list[str] = []
        for index, product in enumerate(ELEVATION_PRODUCTS):
            check_cancelled()
            emit(
                JobState.DISCOVERING,
                0.05 + 0.9 * index / len(ELEVATION_PRODUCTS),
                f"Checking {product.value} ({index + 1}/{len(ELEVATION_PRODUCTS)})",
            )
            product_job = DiscoveryJob(
                task_id=job.task_id,
                import_id=job.import_id,
                bounds=job.bounds,
                product=product,
                elevation_resolution_metres=None,
                use_imagery=False,
                imagery_gsd_metres=None,
                region=job.region,
            )
            try:
                discovery = discover_sources(_create_plan(product_job), provider)
                availability.append(
                    {
                        "product": product.value,
                        "status": "AVAILABLE",
                        "file_count": len(discovery.items),
                    }
                )
            except NoCoverageError:
                availability.append(
                    {"product": product.value, "status": "NO_COVERAGE", "file_count": 0}
                )
            except (CatalogContractChanged, ProviderUnavailableError) as exc:
                availability.append(
                    {"product": product.value, "status": "UNKNOWN", "file_count": 0}
                )
                warnings.append(f"{product.value}: {exc}")
        terminal = JobState.COMPLETE_WITH_WARNINGS if warnings else JobState.COMPLETE
        write_result(
            result_path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": job.task_id,
                "import_id": job.import_id,
                "state": terminal.value,
                "availability": availability,
                "warnings": warnings,
            },
        )
        emit(terminal, 1.0, "Product availability check completed")
        return terminal
    except JobCancelled as exc:
        return _finish_error(result_path, emit, JobState.CANCELLED, str(exc))
    except (JobFormatError, UserInputError) as exc:
        return _finish_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def run_delivery_job(
    job_path: Path,
    cnig_factory: Callable[[], CNIGPortalClient] = CNIGPortalClient,
    imagery_factory: Callable[[], PNOAWMSClient] = PNOAWMSClient,
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> JobState:
    """Discover and download validated elevation and optional PNOA sources."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def cancelled() -> bool:
        return is_cancellation_requested(job_path.parent)

    started_at = monotonic()
    discovery_finished_at = started_at
    delivery_finished_at = started_at
    try:
        emit(JobState.VALIDATING, 0.02, "Validating data delivery job")
        job = read_discovery_job(job_path)
        plan = _create_plan(job)
        local_imagery = (
            inspect_local_imagery(Path(job.local_imagery_path))
            if job.local_imagery_path is not None
            else None
        )
        if local_imagery is not None and (
            local_imagery.bounds != job.local_imagery_bounds
            or local_imagery.width != job.local_imagery_width
            or local_imagery.height != job.local_imagery_height
        ):
            raise JobFormatError("Local imagery changed after the job was created")
        if cancelled():
            raise JobCancelled("Data delivery was cancelled")
        if job.local_elevation_paths:
            local_paths = tuple(Path(path) for path in job.local_elevation_paths)
            elevation_client: ElevationDownloader = _LocalElevationClient(local_paths)
            emit(JobState.DISCOVERING, 0.05, "Confirming local elevation sources")
            discovery = _local_discovery(job)
        else:
            portal = cnig_factory()
            elevation_client = portal
            emit(JobState.DISCOVERING, 0.05, "Confirming current CNIG elevation sources")
            discovery = discover_sources(plan, portal)
        discovery_finished_at = monotonic()
        imagery_requests = plan_imagery_tiles(plan)
        imagery_count = len(imagery_requests)
        file_count = len(discovery.items) + imagery_count
        last_transfer_event_time = 0.0

        def report(transfer: TransferProgress) -> None:
            nonlocal last_transfer_event_time
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
            now = monotonic()
            complete = (
                transfer.cached
                or (
                    transfer.expected_bytes is not None
                    and transfer.written_bytes >= transfer.expected_bytes
                )
            )
            if (
                last_transfer_event_time
                and not complete
                and now - last_transfer_event_time < 0.25
            ):
                return
            last_transfer_event_time = now
            offset = transfer.file_index
            state = JobState.DOWNLOADING_ELEVATION
            if transfer.kind == "imagery":
                offset += len(discovery.items)
                state = JobState.DOWNLOADING_IMAGERY
            fraction = (
                transfer.written_bytes / transfer.expected_bytes
                if transfer.expected_bytes
                else 0.0
            )
            progress = 0.1 + 0.85 * min(1.0, (offset + fraction) / max(1, file_count))
            message = _transfer_message(transfer)
            emit(state, progress, message)

        delivered = deliver_plan_sources(
            plan,
            discovery,
            job_path.parents[2],
            elevation_client,
            imagery_factory(),
            report,
            cancelled,
            local_paths if job.local_elevation_paths else (),
        )
        delivery_finished_at = monotonic()

        def report_processing(completed: int, total: int) -> None:
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
            progress = 0.95 + 0.04 * completed / max(1, total)
            emit(
                JobState.PROCESSING_ELEVATION,
                progress,
                f"Processing terrain tile {min(completed + 1, total)} of {total}"
                if completed < total
                else f"Processed {total} terrain tile(s)",
            )

        processed_directory = job_path.parents[2] / "processed" / job.task_id
        processed_tiles = elevation_processor(
            delivered.elevation_paths,
            plan,
            report_processing,
            region=job.region,
        )
        def report_written(completed: int, total: int) -> None:
            emit(
                JobState.PROCESSING_ELEVATION,
                0.99,
                f"Writing terrain tile {completed} of {total}",
            )

        processed_payload = _write_processed_tiles(
            processed_tiles,
            processed_directory,
            cancelled,
            report_written,
        )
        terminal_state = (
            JobState.COMPLETE_WITH_WARNINGS if delivered.warnings else JobState.COMPLETE
        )
        result_imagery_paths = (
            [str(local_imagery.path)]
            if local_imagery is not None
            else [str(path) for path in delivered.imagery_paths]
        )
        result_imagery = (
            [
                {
                    "path": str(local_imagery.path),
                    "bounds": asdict(local_imagery.bounds),
                    "width": local_imagery.width,
                    "height": local_imagery.height,
                    "gsd_metres": local_imagery.gsd_metres,
                }
            ]
            if local_imagery is not None
            else [
                {
                    "path": str(path),
                    "bounds": asdict(request.bounds),
                    "width": request.width,
                    "height": request.height,
                    "gsd_metres": request.gsd_metres,
                }
                for path, request in zip(
                    delivered.imagery_paths, imagery_requests, strict=False
                )
            ]
        )
        payload = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "task_id": job.task_id,
            "import_id": job.import_id,
            "state": terminal_state.value,
            "warnings": list(delivered.warnings),
            "request": {
                "bounds_wgs84": asdict(job.bounds),
                "roi_geometry_wgs84": (
                    None if job.region is None else job.region.to_geojson_geometry()
                ),
                "product": job.product.value,
                "elevation_resolution_metres": plan.elevation_resolution_metres,
                "use_imagery": job.use_imagery,
                "imagery_gsd_metres": (
                    local_imagery.gsd_metres
                    if local_imagery is not None
                    else None if plan.imagery is None else plan.imagery.gsd_metres
                ),
                "manual_tile_rows": plan.manual_tile_rows,
                "manual_tile_columns": plan.manual_tile_columns,
            },
            "crs": [asdict(work_area.crs) for work_area in plan.work_areas],
            "sources": [
                {
                    **asdict(item),
                    "detail_url": (
                        None
                        if job.local_elevation_paths
                        else f"{BASE_URL}detalleArchivo?sec={item.sequential_id}"
                    ),
                }
                for item in discovery.items
            ],
            "provenance": {
                "source": (
                    "User-provided local elevation raster"
                    if job.local_elevation_paths
                    else "Instituto Geográfico Nacional de España (IGN-CNIG)"
                ),
                "portal_url": "https://centrodedescargas.cnig.es/",
                "data_policy_url": (
                    "https://centrodedescargas.cnig.es/CentroDescargas/politica-datos"
                ),
                "license": "CC BY 4.0-compatible IGN-CNIG data terms",
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
                "pnoa_wms_url": (
                    WMS_URL if job.use_imagery and local_imagery is None else None
                ),
                "pnoa_layer": (
                    PNOA_LAYER if job.use_imagery and local_imagery is None else None
                ),
            },
            "elevation_paths": [str(path) for path in delivered.elevation_paths],
            "imagery_paths": result_imagery_paths,
            "imagery": result_imagery,
            "processed_elevation": processed_payload,
            "cache_reuse": {
                "elevation_files": delivered.cached_elevation_count,
                "imagery_files": delivered.cached_imagery_count,
            },
            "timings_seconds": {
                "discovery": round(discovery_finished_at - started_at, 3),
                "delivery": round(delivery_finished_at - discovery_finished_at, 3),
                "processing": round(monotonic() - delivery_finished_at, 3),
                "total": round(monotonic() - started_at, 3),
            },
        }
        write_result(result_path, payload)
        emit(
            terminal_state,
            1.0,
            (
                delivered.warnings[0]
                if delivered.warnings
                else f"Prepared {len(delivered.elevation_paths)} elevation and "
                f"{len(result_imagery_paths)} imagery and "
                f"{len(processed_tiles)} processed terrain tile(s)"
            ),
        )
        return terminal_state
    except JobCancelled as exc:
        return _finish_error(result_path, emit, JobState.CANCELLED, str(exc))
    except NoCoverageError as exc:
        return _finish_error(result_path, emit, JobState.NO_COVERAGE, str(exc))
    except CatalogContractChanged as exc:
        return _finish_error(result_path, emit, JobState.PROVIDER_CHANGED, str(exc))
    except ProviderUnavailableError as exc:
        return _finish_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (
        DownloadAuthorizationRequired,
        DownloadIntegrityError,
        JobFormatError,
        RasterFormatError,
        UserInputError,
        ValueError,
    ) as exc:
        return _finish_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def _transfer_message(transfer: TransferProgress) -> str:
    kind = {
        "elevation": "elevation",
        "imagery": "PNOA imagery",
        "dtm": "DTM",
        "dsm": "DSM",
    }.get(transfer.kind, transfer.kind)
    position = f"{transfer.file_index + 1}/{transfer.file_count}"
    if transfer.cached:
        return f"Using cached {kind} {position}: {transfer.filename}"
    written_mib = transfer.written_bytes / 1_048_576
    if transfer.expected_bytes:
        expected_mib = transfer.expected_bytes / 1_048_576
        percentage = min(100.0, transfer.written_bytes / transfer.expected_bytes * 100.0)
        size = f"{written_mib:.1f}/{expected_mib:.1f} MiB, {percentage:.0f}%"
    else:
        size = f"{written_mib:.1f} MiB"
    return f"Downloading {kind} {position}: {transfer.filename} ({size})"


def _write_processed_tiles(
    tiles: tuple[ProcessedElevationTile, ...],
    directory: Path,
    cancellation_requested: Callable[[], bool],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for processed in tiles:
        if cancellation_requested():
            raise JobCancelled("Elevation output writing was cancelled")
        filename = (
            f"elevation_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
            f"r{processed.tile.row}_c{processed.tile.column}.npy"
        )
        output_path = directory / filename
        write_elevation_array(output_path, processed.data)
        payload.append(
            {
                "path": str(output_path),
                "bounds": asdict(processed.tile.bounds),
                "rows": processed.tile.rows,
                "columns": processed.tile.columns,
                "nodata": processed.nodata,
                "overlap_valid_pixels": processed.overlap_valid_pixels,
                "conflicting_valid_pixels": processed.conflicting_valid_pixels,
                "maximum_overlap_difference": processed.maximum_overlap_difference,
            }
        )
        if progress_callback is not None:
            progress_callback(len(payload), len(tiles))
    return payload


def _write_processed_bathymetry(
    tiles: tuple[ProcessedBathymetryTile, ...],
    directory: Path,
    cancellation_requested: Callable[[], bool],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for processed in tiles:
        if cancellation_requested():
            raise JobCancelled("Bathymetry output writing was cancelled")
        stem = (
            f"bathymetry_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
            f"r{processed.tile.row}_c{processed.tile.column}"
        )
        elevation_path = directory / f"{stem}.npy"
        tid_path = directory / f"{stem}_tid.npy"
        write_elevation_array(elevation_path, processed.elevation)
        write_quality_array(tid_path, processed.tid)
        payload.append(
            {
                "path": str(elevation_path),
                "tid_path": str(tid_path),
                "bounds": asdict(processed.tile.bounds),
                "rows": processed.tile.rows,
                "columns": processed.tile.columns,
                "nodata": processed.nodata,
            }
        )
    return payload


def _create_plan(job: DiscoveryJob) -> ImportPlan:
    local_native_resolution = None
    local_projected_bounds = None
    if job.local_elevation_paths:
        from ..core.local_elevation import inspect_local_elevation

        inspection = inspect_local_elevation(
            tuple(Path(path) for path in job.local_elevation_paths)
        )
        local_native_resolution = inspection.native_resolution_metres
        local_projected_bounds = inspection.projected_bounds
    return create_import_plan(
        job.bounds,
        job.product,
        job.elevation_resolution_metres,
        job.use_imagery and job.local_imagery_path is None,
        job.imagery_gsd_metres,
        job.manual_tile_rows,
        job.manual_tile_columns,
        job.maximum_elevation_samples,
        job.maximum_imagery_pixels,
        native_resolution_override=local_native_resolution,
        projected_bounds_override=local_projected_bounds,
    )


def _local_discovery(job: DiscoveryJob) -> DiscoveryResult:
    """Validate local files and represent them as traceable discovery items."""

    paths = tuple(Path(value) for value in job.local_elevation_paths)
    if not paths:
        raise UserInputError("No local elevation rasters were provided")
    if len({path.name.casefold() for path in paths}) != len(paths):
        raise UserInputError("Local elevation raster filenames must be unique")
    items: list[CatalogItem] = []
    for index, path in enumerate(paths):
        if not path.is_file():
            raise UserInputError(f"Local elevation raster does not exist: {path}")
        open_float_tile_reader(path)
        size_mb = path.stat().st_size / 1_000_000
        items.append(
            CatalogItem(
                job.product,
                path.name,
                "LOCAL_COG",
                f"local-{index}",
                size_mb=size_mb,
            )
        )
    return DiscoveryResult(tuple(items), len(items), 0)


def _finish_error(
    result_path: Path,
    emit: Callable[[JobState, float, str], None],
    state: JobState,
    message: str,
) -> JobState:
    write_result(
        result_path,
        {"schema_version": RESULT_SCHEMA_VERSION, "state": state.value, "error": message},
    )
    emit(state, 1.0, message)
    return state
