"""Validate and discover user-provided local raster sources."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from ..core import ImportPlan, create_import_plan, inspect_local_elevation, inspect_local_imagery
from ..errors import JobCancelled, JobFormatError, RasterFormatError, UserInputError
from ..models import CatalogItem
from ..providers.cnig_discovery import DiscoveryResult
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)


def run_local_discovery_job(job_path: Path) -> JobState:
    """Validate local rasters and persist their source summary."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    try:
        emit(JobState.VALIDATING, 0.05, "Validating local raster job")
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Local source discovery was cancelled")
        job = read_discovery_job(job_path)
        if not job.local_elevation_paths:
            raise JobFormatError("Local discovery requires elevation raster paths")
        create_local_import_plan(job)
        if job.local_imagery_path is not None:
            imagery = inspect_local_imagery(Path(job.local_imagery_path))
            if (
                imagery.bounds != job.local_imagery_bounds
                or imagery.width != job.local_imagery_width
                or imagery.height != job.local_imagery_height
            ):
                raise JobFormatError("Local imagery changed after the job was created")
        emit(JobState.DISCOVERING, 0.25, "Validating local elevation sources")
        discovery = discover_local_sources(job)
        write_result(
            result_path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": job.task_id,
                "import_id": job.import_id,
                "state": JobState.COMPLETE.value,
                "advertised_items": discovery.advertised_items,
                "ignored_items": discovery.ignored_items,
                "estimated_download_mb": discovery.estimated_download_mb,
                "items": [asdict(item) for item in discovery.items],
            },
        )
        emit(JobState.COMPLETE, 1.0, f"Validated {len(discovery.items)} local source file(s)")
        return JobState.COMPLETE
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except (JobFormatError, RasterFormatError, UserInputError) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def create_local_import_plan(job: DiscoveryJob) -> ImportPlan:
    """Build an import plan from validated local elevation metadata."""

    paths = tuple(Path(path) for path in job.local_elevation_paths)
    inspection = inspect_local_elevation(paths)
    return create_import_plan(
        job.bounds,
        job.product,
        job.elevation_resolution_metres,
        False,
        None,
        job.manual_tile_rows,
        job.manual_tile_columns,
        job.maximum_elevation_samples,
        job.maximum_imagery_pixels,
        native_resolution_override=inspection.native_resolution_metres,
        projected_bounds_override=inspection.projected_bounds,
    )


def discover_local_sources(job: DiscoveryJob) -> DiscoveryResult:
    """Validate local files and expose them through the source result contract."""

    paths = tuple(Path(value) for value in job.local_elevation_paths)
    inspection = inspect_local_elevation(paths)
    items = tuple(
        CatalogItem(
            job.product,
            path.name,
            "LOCAL_COG",
            f"local-{index}",
            size_mb=path.stat().st_size / 1_000_000,
        )
        for index, path in enumerate(inspection.paths)
    )
    return DiscoveryResult(items, len(items), 0)
