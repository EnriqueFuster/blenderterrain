"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..core import (
    ImportPlan,
    ProcessedElevationTile,
    TransferProgress,
    create_import_plan,
    deliver_plan_sources,
    discover_sources,
    plan_imagery_tiles,
    process_elevation_tiles,
)
from ..core.discovery import CatalogDiscoveryProvider
from ..errors import (
    CatalogContractChanged,
    DownloadAuthorizationRequired,
    DownloadIntegrityError,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    UserInputError,
)
from ..io.elevation_output import write_elevation_array
from ..providers.cnig_portal import CNIGPortalClient
from ..providers.pnoa_wms import PNOAWMSClient
from .models import DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)

ProviderFactory = Callable[[], CatalogDiscoveryProvider]
ElevationProcessor = Callable[
    [tuple[Path, ...], ImportPlan], tuple[ProcessedElevationTile, ...]
]


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
        emit(JobState.DISCOVERING, 0.25, "Discovering CNIG elevation sources")
        discovery = discover_sources(plan, provider_factory())
        check_cancelled()
        payload = {
            "schema_version": 1,
            "job_id": job.job_id,
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

    try:
        emit(JobState.VALIDATING, 0.02, "Validating data delivery job")
        job = read_discovery_job(job_path)
        plan = _create_plan(job)
        if cancelled():
            raise JobCancelled("Data delivery was cancelled")
        cnig = cnig_factory()
        emit(JobState.DISCOVERING, 0.05, "Confirming current CNIG elevation sources")
        discovery = discover_sources(plan, cnig)
        imagery_count = len(plan_imagery_tiles(plan))
        file_count = len(discovery.items) + imagery_count

        def report(transfer: TransferProgress) -> None:
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
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
            emit(
                state,
                progress,
                f"Downloading {transfer.filename} ({transfer.written_bytes / 1_048_576:.1f} MiB)",
            )

        delivered = deliver_plan_sources(
            plan,
            discovery,
            job_path.parents[2],
            cnig,
            imagery_factory(),
            report,
            cancelled,
        )
        emit(JobState.PROCESSING_ELEVATION, 0.96, "Processing final elevation tiles")
        processed_directory = job_path.parents[2] / "processed" / job.job_id
        processed_tiles = elevation_processor(delivered.elevation_paths, plan)
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
        payload = {
            "schema_version": 1,
            "job_id": job.job_id,
            "state": JobState.COMPLETE.value,
            "elevation_paths": [str(path) for path in delivered.elevation_paths],
            "imagery_paths": [str(path) for path in delivered.imagery_paths],
            "processed_elevation": processed_payload,
        }
        write_result(result_path, payload)
        emit(
            JobState.COMPLETE,
            1.0,
            f"Prepared {len(delivered.elevation_paths)} elevation and "
            f"{len(delivered.imagery_paths)} imagery and "
            f"{len(processed_tiles)} processed terrain tile(s)",
        )
        return JobState.COMPLETE
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
        UserInputError,
        ValueError,
    ) as exc:
        return _finish_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def _create_plan(job: DiscoveryJob) -> ImportPlan:
    return create_import_plan(
        job.bounds,
        job.product,
        job.elevation_resolution_metres,
        job.use_imagery,
        job.imagery_gsd_metres,
    )


def _finish_error(
    result_path: Path,
    emit: Callable[[JobState, float, str], None],
    state: JobState,
    message: str,
) -> JobState:
    write_result(
        result_path,
        {"schema_version": 1, "state": state.value, "error": message},
    )
    emit(state, 1.0, message)
    return state
