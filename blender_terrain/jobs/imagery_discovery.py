"""Background preflight for dynamic imagery products."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..catalog import DatasetKind
from ..errors import (
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderContractChanged,
    ProviderUnavailableError,
    UserInputError,
)
from ..providers.sentinel2 import PRODUCT_ID as SENTINEL2_PRODUCT_ID
from ..providers.sentinel2 import Sentinel2CatalogClient, Sentinel2Scene, discover_sentinel2_scenes
from .models import RESULT_SCHEMA_VERSION, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_acquisition_job,
    write_result,
)


def run_imagery_discovery_job(
    job_path: Path,
    catalog_factory: Callable[[], Sentinel2CatalogClient] = Sentinel2CatalogClient,
) -> JobState:
    """Check the confirmed Sentinel-2 policy without downloading raster bands."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    try:
        emit(JobState.VALIDATING, 0.05, "Validating imagery discovery request")
        job = read_acquisition_job(job_path)
        selection = job.plan.selections.for_kind(DatasetKind.IMAGERY)
        if selection is None or selection.product_id != SENTINEL2_PRODUCT_ID:
            raise UserInputError("Dynamic imagery discovery requires Sentinel-2")
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Sentinel-2 discovery was cancelled")
        emit(JobState.DISCOVERING, 0.25, "Searching Sentinel-2 scenes")
        scenes = discover_sentinel2_scenes(
            job.plan.request.roi,
            selection.temporal_policy,
            catalog_factory(),
        )
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Sentinel-2 discovery was cancelled")
        payload = _result_payload(scenes)
        write_result(result_path, payload)
        emit(
            JobState.COMPLETE,
            1.0,
            f"Found {len(scenes)} Sentinel-2 scene(s) covering the ROI",
        )
        return JobState.COMPLETE
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except NoCoverageError as exc:
        return finish_job_error(result_path, emit, JobState.NO_COVERAGE, str(exc))
    except ProviderUnavailableError as exc:
        return finish_job_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (JobFormatError, ProviderContractChanged, UserInputError, ValueError) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def _result_payload(scenes: tuple[Sentinel2Scene, ...]) -> dict[str, object]:
    dates = tuple(scene.acquired_at for scene in scenes)
    clouds = tuple(scene.cloud_cover_percent for scene in scenes)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "state": JobState.COMPLETE.value,
        "sentinel2": {
            "scene_count": len(scenes),
            "scene_ids": [scene.id for scene in scenes],
            "earliest_acquisition": min(dates),
            "latest_acquisition": max(dates),
            "minimum_cloud_percent": min(clouds),
            "maximum_cloud_percent": max(clouds),
            "coverage_status": "complete",
        },
    }
