"""Dependencies used by the pre-catalog Spanish delivery job."""

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
    inspect_local_imagery,
    process_elevation_tiles,
)
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
from ..models import CatalogItem
from ..providers.cnig_delivery import ElevationDownloader, deliver_plan_sources
from ..providers.cnig_discovery import discover_sources
from ..providers.cnig_portal import BASE_URL, CNIGPortalClient
from ..providers.pnoa_planning import plan_pnoa_tiles
from ..providers.pnoa_wms import PNOA_LAYER, WMS_URL, PNOAWMSClient
from .legacy_discovery import create_legacy_import_plan, discover_local_sources
from .models import RESULT_SCHEMA_VERSION, JobState, ProgressEvent
from .output import transfer_message, write_processed_tiles
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_discovery_job,
    write_result,
)


class LocalElevationClient:
    """Expose user-provided files through the legacy downloader contract."""

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
    """Callable contract for the legacy elevation processing stage."""

    def __call__(
        self,
        source_paths: tuple[Path, ...],
        plan: ImportPlan,
        progress_callback: Callable[[int, int], None] | None = None,
        maximum_source_window_pixels: int = 4_194_304,
        region: RegionOfInterest | None = None,
    ) -> tuple[ProcessedElevationTile, ...]: ...


def run_delivery_job(
    job_path: Path,
    cnig_factory: Callable[[], CNIGPortalClient] = CNIGPortalClient,
    imagery_factory: Callable[[], PNOAWMSClient] = PNOAWMSClient,
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> JobState:
    """Discover and prepare sources using the Spanish baseline job format."""

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
        if cancelled():
            raise JobCancelled("Data delivery was cancelled")
        local_paths: tuple[Path, ...] = ()
        if job.local_elevation_paths:
            local_paths = tuple(Path(path) for path in job.local_elevation_paths)
            elevation_client: ElevationDownloader = LocalElevationClient(local_paths)
            emit(JobState.DISCOVERING, 0.05, "Confirming local elevation sources")
            discovery = discover_local_sources(job)
        else:
            portal = cnig_factory()
            elevation_client = portal
            emit(JobState.DISCOVERING, 0.05, "Confirming current CNIG elevation sources")
            discovery = discover_sources(plan, portal)
        discovery_finished_at = monotonic()
        imagery_requests = plan_pnoa_tiles(plan)
        file_count = len(discovery.items) + len(imagery_requests)
        last_transfer_event_time = 0.0

        def report(transfer: TransferProgress) -> None:
            nonlocal last_transfer_event_time
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
            now = monotonic()
            complete = transfer.cached or (
                transfer.expected_bytes is not None
                and transfer.written_bytes >= transfer.expected_bytes
            )
            if last_transfer_event_time and not complete and now - last_transfer_event_time < 0.25:
                return
            last_transfer_event_time = now
            offset = transfer.file_index
            state = JobState.DOWNLOADING_ELEVATION
            if transfer.kind == "imagery":
                offset += len(discovery.items)
                state = JobState.DOWNLOADING_IMAGERY
            fraction = (
                transfer.written_bytes / transfer.expected_bytes if transfer.expected_bytes else 0.0
            )
            progress = 0.1 + 0.85 * min(1.0, (offset + fraction) / max(1, file_count))
            emit(state, progress, transfer_message(transfer))

        delivered = deliver_plan_sources(
            plan,
            discovery,
            job_path.parents[2],
            elevation_client,
            imagery_factory(),
            report,
            cancelled,
            local_paths,
        )
        delivery_finished_at = monotonic()

        def report_processing(completed: int, total: int) -> None:
            if cancelled():
                raise JobCancelled("Data delivery was cancelled")
            emit(
                JobState.PROCESSING_ELEVATION,
                0.95 + 0.04 * completed / max(1, total),
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

        processed_payload = write_processed_tiles(
            processed_tiles, processed_directory, cancelled, report_written
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
                for path, request in zip(delivered.imagery_paths, imagery_requests, strict=False)
            ]
        )
        write_result(
            result_path,
            {
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
                        else None
                        if plan.imagery is None
                        else plan.imagery.gsd_metres
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
            },
        )
        emit(
            terminal_state,
            1.0,
            delivered.warnings[0]
            if delivered.warnings
            else f"Prepared {len(delivered.elevation_paths)} elevation and "
            f"{len(result_imagery_paths)} imagery and "
            f"{len(processed_tiles)} processed terrain tile(s)",
        )
        return terminal_state
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except NoCoverageError as exc:
        return finish_job_error(result_path, emit, JobState.NO_COVERAGE, str(exc))
    except CatalogContractChanged as exc:
        return finish_job_error(result_path, emit, JobState.PROVIDER_CHANGED, str(exc))
    except ProviderUnavailableError as exc:
        return finish_job_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (
        DownloadAuthorizationRequired,
        DownloadIntegrityError,
        JobFormatError,
        RasterFormatError,
        UserInputError,
        ValueError,
    ) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))
