"""Run CNIG source discovery and product availability jobs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..catalog import load_bundled_catalog
from ..core import ImportPlan, create_import_plan
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
from ..providers.cnig_discovery import CatalogDiscoveryProvider, discover_sources
from ..providers.cnig_portal import CNIGPortalClient
from ..providers.spain_crs import split_spain_bbox_by_utm_zone
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


def run_cnig_discovery_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Discover CNIG elevation files and persist their source summary."""

    events_path = job_path.with_name("events.jsonl")
    result_path = job_path.with_name("result.json")
    sequence = 0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal sequence
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        sequence += 1

    def check_cancelled() -> None:
        if is_cancellation_requested(job_path.parent):
            raise JobCancelled("CNIG source discovery was cancelled")

    try:
        emit(JobState.VALIDATING, 0.05, "Validating CNIG discovery job")
        check_cancelled()
        job = read_discovery_job(job_path)
        if job.local_elevation_paths:
            raise JobFormatError("CNIG discovery cannot process local elevation paths")
        plan = create_cnig_import_plan(job)
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
        emit(JobState.COMPLETE, 1.0, f"Found {len(discovery.items)} CNIG source file(s)")
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


def run_cnig_availability_job(
    job_path: Path,
    provider_factory: ProviderFactory = CNIGPortalClient,
) -> JobState:
    """Check every CNIG elevation product without downloading raster data."""

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
        emit(JobState.VALIDATING, 0.02, "Validating CNIG product availability request")
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
                discovery = discover_sources(create_cnig_import_plan(product_job), provider)
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
        emit(terminal, 1.0, "CNIG product availability check completed")
        return terminal
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except (JobFormatError, UserInputError) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))


def create_cnig_import_plan(job: DiscoveryJob) -> ImportPlan:
    """Build an import plan for the Spanish CNIG product in a discovery job."""

    if job.local_elevation_paths:
        raise JobFormatError("CNIG planning cannot use local elevation paths")
    native_resolution = (
        load_bundled_catalog()
        .product(job.product.value)
        .capabilities.native_resolution_m
    )
    return create_import_plan(
        job.bounds,
        job.product,
        job.elevation_resolution_metres,
        job.use_imagery,
        job.imagery_gsd_metres,
        job.manual_tile_rows,
        job.manual_tile_columns,
        job.maximum_elevation_samples,
        job.maximum_imagery_pixels,
        native_resolution_override=native_resolution,
        work_areas_override=split_spain_bbox_by_utm_zone(job.bounds),
    )
