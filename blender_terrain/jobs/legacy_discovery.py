"""Run the pre-catalog CNIG discovery and availability jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..core import ImportPlan, create_import_plan, inspect_local_imagery
from ..errors import (
    CatalogContractChanged,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    RasterFormatError,
    UserInputError,
)
from ..models import DatasetProduct
from ..providers.cnig_discovery import (
    CatalogDiscoveryProvider,
    discover_sources,
)
from ..providers.cnig_portal import CNIGPortalClient
from .local import create_local_import_plan, discover_local_sources
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)

ProviderFactory = Callable[[], CatalogDiscoveryProvider]
ELEVATION_PRODUCTS = tuple(
    product for product in DatasetProduct if product is not DatasetProduct.PNOA_MA
)


def run_discovery_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Run legacy CNIG discovery and persist its terminal result."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def check_cancelled() -> None:
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("Source discovery was cancelled")

    try:
        emit(JobState.VALIDATING, 0.05, "Validating discovery job")
        check_cancelled()
        job = read_discovery_job(job_path)
        plan = create_legacy_import_plan(job)
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
            discovery = discover_local_sources(job)
        else:
            emit(JobState.DISCOVERING, 0.25, "Discovering CNIG elevation sources")
            discovery = discover_sources(plan, provider_factory())
        check_cancelled()
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
        emit(JobState.COMPLETE, 1.0, f"Found {len(discovery.items)} source file(s)")
        return JobState.COMPLETE
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except NoCoverageError as exc:
        return finish_job_error(result_path, emit, JobState.NO_COVERAGE, str(exc))
    except CatalogContractChanged as exc:
        return finish_job_error(result_path, emit, JobState.PROVIDER_CHANGED, str(exc))
    except ProviderUnavailableError as exc:
        return finish_job_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (JobFormatError, RasterFormatError, UserInputError) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def run_availability_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Check every legacy CNIG elevation product without downloading data."""

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
                discovery = discover_sources(create_legacy_import_plan(product_job), provider)
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
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except (JobFormatError, UserInputError) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def create_legacy_import_plan(job: DiscoveryJob) -> ImportPlan:
    """Build a plan from the job format used by the Spanish baseline."""

    if job.local_elevation_paths:
        return create_local_import_plan(job)
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
    )
