"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..core import ImportPlan, create_import_plan, discover_sources
from ..core.discovery import CatalogDiscoveryProvider
from ..errors import (
    CatalogContractChanged,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    UserInputError,
)
from ..providers.cnig_portal import CNIGPortalClient
from .models import DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)

ProviderFactory = Callable[[], CatalogDiscoveryProvider]


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
