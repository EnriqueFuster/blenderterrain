"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from ..core import (
    ImportPlan,
    ProcessedElevationTile,
    RegionOfInterest,
    TransferProgress,
    create_import_plan,
    deliver_plan_sources,
    discover_sources,
    plan_imagery_tiles,
    process_elevation_tiles,
)
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
from ..io.bigtiff_tiles import BigTiffFloatTileReader
from ..io.elevation_output import write_elevation_array
from ..models import CatalogItem
from ..providers.cnig_portal import BASE_URL, CNIGPortalClient
from ..providers.pnoa_wms import PNOA_LAYER, WMS_URL, PNOAWMSClient
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)

ProviderFactory = Callable[[], CatalogDiscoveryProvider]


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

    try:
        emit(JobState.VALIDATING, 0.02, "Validating data delivery job")
        job = read_discovery_job(job_path)
        plan = _create_plan(job)
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
        processed_payload: list[dict[str, object]] = []
        for processed in processed_tiles:
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
            filename = (
                f"elevation_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
                f"r{processed.tile.row}_c{processed.tile.column}.npy"
            )
            output_path = processed_directory / filename
            write_elevation_array(output_path, processed.data)
            processed_payload.append(
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
            emit(
                JobState.PROCESSING_ELEVATION,
                0.99,
                f"Writing terrain tile {len(processed_payload)} of {len(processed_tiles)}",
            )
        terminal_state = (
            JobState.COMPLETE_WITH_WARNINGS if delivered.warnings else JobState.COMPLETE
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
                    None if plan.imagery is None else plan.imagery.gsd_metres
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
                "pnoa_wms_url": WMS_URL if job.use_imagery else None,
                "pnoa_layer": PNOA_LAYER if job.use_imagery else None,
            },
            "elevation_paths": [str(path) for path in delivered.elevation_paths],
            "imagery_paths": [str(path) for path in delivered.imagery_paths],
            "imagery": [
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
            ],
            "processed_elevation": processed_payload,
        }
        write_result(result_path, payload)
        emit(
            terminal_state,
            1.0,
            (
                delivered.warnings[0]
                if delivered.warnings
                else f"Prepared {len(delivered.elevation_paths)} elevation and "
                f"{len(delivered.imagery_paths)} imagery and "
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
    kind = "elevation" if transfer.kind == "elevation" else "PNOA imagery"
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


def _create_plan(job: DiscoveryJob) -> ImportPlan:
    return create_import_plan(
        job.bounds,
        job.product,
        job.elevation_resolution_metres,
        job.use_imagery,
        job.imagery_gsd_metres,
        job.manual_tile_rows,
        job.manual_tile_columns,
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
        BigTiffFloatTileReader(path)
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
