"""Validate and discover user-provided local raster sources."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from ..core import (
    ImportPlan,
    create_import_plan,
    inspect_local_elevation,
    inspect_local_imagery,
    process_elevation_tiles,
)
from ..errors import JobCancelled, JobFormatError, RasterFormatError, UserInputError
from ..models import CatalogItem
from ..providers.cnig_discovery import DiscoveryResult
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent
from .output import write_processed_tiles
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)
from .terrain import ElevationProcessor


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


def run_local_delivery_job(
    job_path: Path,
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> JobState:
    """Process validated local rasters and publish Blender-ready output."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0
    started_at = monotonic()

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def cancelled() -> bool:
        return is_cancellation_requested(job_path.parent)

    try:
        emit(JobState.VALIDATING, 0.02, "Validating local raster delivery")
        job = read_discovery_job(job_path)
        if not job.local_elevation_paths:
            raise JobFormatError("Local delivery requires elevation raster paths")
        plan = create_local_import_plan(job)
        elevation_paths = tuple(Path(path) for path in job.local_elevation_paths)
        imagery = (
            None
            if job.local_imagery_path is None
            else inspect_local_imagery(Path(job.local_imagery_path))
        )
        if imagery is not None and (
            imagery.bounds != job.local_imagery_bounds
            or imagery.width != job.local_imagery_width
            or imagery.height != job.local_imagery_height
        ):
            raise JobFormatError("Local imagery changed after the job was created")
        if cancelled():
            raise JobCancelled("Local raster delivery was cancelled")

        def report_processing(completed: int, total: int) -> None:
            if cancelled():
                raise JobCancelled("Local raster delivery was cancelled")
            emit(
                JobState.PROCESSING_ELEVATION,
                0.05 + 0.9 * completed / max(1, total),
                f"Processing terrain tile {completed}/{total}",
            )

        processed_tiles = elevation_processor(
            elevation_paths,
            plan,
            report_processing,
            region=job.region,
        )
        processed_payload = write_processed_tiles(
            processed_tiles,
            job_path.parents[2] / "processed" / job.task_id,
            cancelled,
        )
        inspection = inspect_local_elevation(elevation_paths)
        imagery_payload = (
            []
            if imagery is None
            else [
                {
                    "path": str(imagery.path),
                    "bounds": asdict(imagery.bounds),
                    "width": imagery.width,
                    "height": imagery.height,
                    "gsd_metres": imagery.gsd_metres,
                }
            ]
        )
        finished_at = monotonic()
        write_result(
            result_path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": job.task_id,
                "import_id": job.import_id,
                "state": JobState.COMPLETE.value,
                "warnings": [],
                "request": {
                    "bounds_wgs84": asdict(job.bounds),
                    "roi_geometry_wgs84": (
                        None if job.region is None else job.region.to_geojson_geometry()
                    ),
                    "product": job.product.value,
                    "elevation_resolution_metres": plan.elevation_resolution_metres,
                    "use_imagery": imagery is not None,
                    "imagery_gsd_metres": None if imagery is None else imagery.gsd_metres,
                    "manual_tile_rows": plan.manual_tile_rows,
                    "manual_tile_columns": plan.manual_tile_columns,
                },
                "crs": [asdict(work_area.crs) for work_area in plan.work_areas],
                "sources": [
                    {
                        "product": job.product.value,
                        "filename": path.name,
                        "format": "LOCAL_COG",
                        "sequential_id": f"local-{index}",
                        "size_mb": path.stat().st_size / 1_000_000,
                        "detail_url": None,
                    }
                    for index, path in enumerate(inspection.paths)
                ],
                "provenance": {
                    "source": "User-provided local elevation raster",
                    "portal_url": None,
                    "data_policy_url": None,
                    "license": None,
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                    "pnoa_wms_url": None,
                    "pnoa_layer": None,
                },
                "elevation_paths": [str(path) for path in inspection.paths],
                "imagery_paths": [] if imagery is None else [str(imagery.path)],
                "imagery": imagery_payload,
                "processed_elevation": processed_payload,
                "cache_reuse": {"elevation_files": 0, "imagery_files": 0},
                "timings_seconds": {
                    "discovery": 0.0,
                    "delivery": 0.0,
                    "processing": round(finished_at - started_at, 3),
                    "total": round(finished_at - started_at, 3),
                },
            },
        )
        emit(
            JobState.COMPLETE,
            1.0,
            f"Prepared {len(processed_tiles)} terrain tile(s) from local rasters",
        )
        return JobState.COMPLETE
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except (JobFormatError, RasterFormatError, UserInputError, ValueError) as exc:
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
