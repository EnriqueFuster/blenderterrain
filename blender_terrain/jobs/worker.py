"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..catalog import load_bundled_catalog
from ..catalog.models import DatasetKind
from ..core import (
    TransferProgress,
    compose_terrain_bathymetry,
    process_elevation_tiles,
)
from ..core.acquisition import RasterAcquirer
from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    RasterFormatError,
    UserInputError,
)
from ..providers.registry import build_raster_acquirers
from .imagery import prepare_confirmed_imagery
from .legacy_delivery import ElevationProcessor
from .legacy_delivery import run_delivery_job as run_delivery_job
from .models import JobState, ProgressEvent
from .output import (
    build_result_payload,
    transfer_message,
    write_composed_tiles,
    write_processed_bathymetry,
    write_processed_tiles,
)
from .storage import (
    append_progress_event,
    finish_job_error,
    is_cancellation_requested,
    read_acquisition_job,
    write_result,
)
from .terrain import PreparedElevation as PreparedElevation
from .terrain import acquire_confirmed_sources as acquire_confirmed_sources
from .terrain import prepare_confirmed_bathymetry as prepare_confirmed_bathymetry
from .terrain import prepare_confirmed_elevation as prepare_confirmed_elevation


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
    last_progress = 0.0

    def emit(state: JobState, progress: float, message: str) -> None:
        nonlocal last_progress, sequence
        progress = max(last_progress, min(1.0, progress))
        append_progress_event(events_path, ProgressEvent(sequence, state, progress, message))
        last_progress = progress
        sequence += 1

    def cancelled() -> bool:
        return is_cancellation_requested(job_path.parent)

    try:
        emit(JobState.VALIDATING, 0.02, "Validating confirmed acquisition")
        job = read_acquisition_job(job_path)
        catalog = load_bundled_catalog()
        has_bathymetry = job.plan.selections.for_kind(DatasetKind.BATHYMETRY) is not None
        has_imagery = job.plan.selections.for_kind(DatasetKind.IMAGERY) is not None
        stage_weights = [
            ("elevation_download", 3.0),
            ("elevation_process", 2.0),
        ]
        if has_bathymetry:
            stage_weights.extend((("bathymetry_download", 1.0), ("bathymetry_process", 2.0)))
        if has_imagery:
            stage_weights.extend((("imagery_download", 3.0), ("imagery_process", 2.0)))
        stage_weights.append(("output", 1.0))
        total_weight = sum(weight for _name, weight in stage_weights)
        stage_ranges: dict[str, tuple[float, float]] = {}
        cursor = 0.03
        for name, weight in stage_weights:
            end = cursor + 0.95 * weight / total_weight
            stage_ranges[name] = (cursor, end)
            cursor = end

        def stage_progress(name: str, fraction: float) -> float:
            start, end = stage_ranges[name]
            return start + (end - start) * min(1.0, max(0.0, fraction))

        def transfer(progress: TransferProgress) -> None:
            file_fraction = (
                progress.written_bytes / progress.expected_bytes
                if progress.expected_bytes
                else float(progress.cached or progress.written_bytes > 0)
            )
            fraction = (progress.file_index + min(1.0, file_fraction)) / max(1, progress.file_count)
            stage = {
                DatasetKind.BATHYMETRY.value: "bathymetry_download",
                DatasetKind.IMAGERY.value: "imagery_download",
            }.get(progress.kind, "elevation_download")
            state = (
                JobState.DOWNLOADING_IMAGERY
                if progress.kind == DatasetKind.IMAGERY.value
                else JobState.DOWNLOADING_ELEVATION
            )
            emit(
                state,
                stage_progress(stage, fraction),
                transfer_message(progress),
            )

        def processing(completed: int, total: int) -> None:
            emit(
                JobState.PROCESSING_ELEVATION,
                stage_progress("elevation_process", completed / max(1, total)),
                f"Processing terrain tile {completed}/{total}",
            )

        emit(
            JobState.DOWNLOADING_ELEVATION,
            stage_progress("elevation_download", 0.0),
            "Downloading selected elevation source",
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
        if has_bathymetry:
            emit(
                JobState.DOWNLOADING_ELEVATION,
                stage_progress("bathymetry_download", 0.0),
                "Downloading GEBCO bathymetry",
            )
        bathymetry = prepare_confirmed_bathymetry(
            job.plan,
            catalog,
            prepared.import_plan,
            job_path.parents[2],
            transfer_callback=transfer,
            processing_callback=lambda completed, total: emit(
                JobState.PROCESSING_ELEVATION,
                stage_progress("bathymetry_process", completed / max(1, total)),
                f"Processing bathymetry tile {completed}/{total}",
            ),
            cancellation_requested=cancelled,
            acquirer_factory=acquirer_factory,
            region=job.region,
        )
        imagery_unavailable = False
        try:
            if has_imagery:
                emit(
                    JobState.DOWNLOADING_IMAGERY,
                    stage_progress("imagery_download", 0.0),
                    "Downloading selected imagery source",
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
                    stage_progress("imagery_process", completed / max(1, total)),
                    f"Processing texture tile {completed}/{total}",
                ),
                cancellation_requested=cancelled,
                acquirer_factory=acquirer_factory,
            )
        except NoCoverageError:
            imagery = None
            imagery_unavailable = True
            emit(
                JobState.PROCESSING_ELEVATION,
                stage_progress("imagery_process", 1.0),
                "WorldCover has no imagery for this ROI; continuing without texture",
            )
        emit(
            JobState.PROCESSING_ELEVATION,
            stage_progress("output", 0.0),
            "Writing prepared terrain data",
        )
        terrain_source_payload: list[dict[str, object]] | None = None
        if bathymetry is None:
            processed_payload = write_processed_tiles(
                prepared.tiles,
                processed_directory,
                cancelled,
            )
        else:
            terrain_source_payload = write_processed_tiles(
                prepared.tiles,
                processed_directory / "terrain_source",
                cancelled,
            )
            processed_payload = write_composed_tiles(
                compose_terrain_bathymetry(prepared.tiles, bathymetry.tiles),
                processed_directory,
                cancelled,
            )
        bathymetry_payload = write_processed_bathymetry(
            () if bathymetry is None else bathymetry.tiles,
            processed_directory / "bathymetry",
            cancelled,
        )
        emit(
            JobState.PROCESSING_ELEVATION,
            stage_progress("output", 0.8),
            "Finalizing provenance and cache results",
        )
        final_state, result_payload = build_result_payload(
            job,
            catalog,
            prepared,
            imagery,
            bathymetry,
            processed_payload,
            terrain_source_payload,
            bathymetry_payload,
            imagery_unavailable,
        )
        write_result(result_path, result_payload)
        emit(
            final_state,
            1.0,
            f"Prepared {len(prepared.tiles)} terrain and "
            f"{len(bathymetry_payload)} bathymetry and "
            f"{0 if imagery is None else len(imagery.tiles)} texture tile(s)",
        )
        return final_state
    except JobCancelled as exc:
        return finish_job_error(result_path, emit, JobState.CANCELLED, str(exc))
    except ProviderUnavailableError as exc:
        return finish_job_error(result_path, emit, JobState.NETWORK_ERROR, str(exc))
    except (
        DownloadIntegrityError,
        JobFormatError,
        RasterFormatError,
        UserInputError,
        ValueError,
    ) as exc:
        return finish_job_error(result_path, emit, JobState.INVALID_DATA, str(exc))
