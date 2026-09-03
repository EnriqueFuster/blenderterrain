"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from ..catalog import load_bundled_catalog
from ..catalog.models import DatasetKind
from ..core import (
    TransferProgress,
    compose_terrain_bathymetry,
    process_elevation_tiles,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer
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
from .models import RESULT_SCHEMA_VERSION, JobState, ProgressEvent
from .output import (
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
        elevation_selection = next(
            selection
            for selection in job.plan.selections.selections
            if selection.kind in {DatasetKind.DTM, DatasetKind.DSM}
        )
        product = catalog.product(elevation_selection.product_id)
        imagery_product = None if imagery is None else catalog.product(imagery.acquired.product_id)
        bathymetry_product = (
            None if bathymetry is None else catalog.product(bathymetry.acquired.product_id)
        )
        source_layers = (
            ([] if prepared.acquired is None else [prepared.acquired])
            + ([] if bathymetry is None else [bathymetry.acquired])
            + ([] if imagery is None else [imagery.acquired])
        )
        elevation_unavailable = prepared.acquired is None
        final_state = (
            JobState.COMPLETE_WITH_WARNINGS
            if elevation_unavailable or imagery_unavailable
            else JobState.COMPLETE
        )
        write_result(
            result_path,
            {
                "schema_version": RESULT_SCHEMA_VERSION,
                "task_id": job.task_id,
                "import_id": job.import_id,
                "state": final_state.value,
                "warnings": list(product.coverage.limitations)
                + (
                    [
                        f"{product.name} has no data for this ROI; terrain was built from "
                        "the confirmed bathymetry source"
                    ]
                    if elevation_unavailable
                    else []
                )
                + (
                    ["WorldCover has no imagery for this ROI; no texture was prepared"]
                    if imagery_unavailable
                    else []
                )
                + (
                    []
                    if bathymetry_product is None
                    else list(bathymetry_product.coverage.limitations)
                )
                + ([] if imagery_product is None else list(imagery_product.coverage.limitations)),
                "request": {
                    "bounds_wgs84": asdict(job.plan.request.roi),
                    "roi_geometry_wgs84": (
                        None if job.region is None else job.region.to_geojson_geometry()
                    ),
                    "product": product.id,
                    "elevation_resolution_metres": (
                        prepared.import_plan.elevation_resolution_metres
                    ),
                    "use_imagery": imagery is not None,
                    "use_bathymetry": bathymetry is not None,
                    "imagery_gsd_metres": (
                        None
                        if prepared.import_plan.imagery is None
                        else prepared.import_plan.imagery.gsd_metres
                    ),
                    "manual_tile_rows": job.manual_tile_rows,
                    "manual_tile_columns": job.manual_tile_columns,
                },
                "crs": [asdict(area.crs) for area in prepared.import_plan.work_areas],
                "sources": [
                    {
                        "provider_id": layer.provider_id,
                        "product_id": layer.product_id,
                        "kind": layer.kind.value,
                        "license": catalog.product(layer.product_id).license.identifier,
                        "attribution": (catalog.product(layer.product_id).license.attribution_text),
                        "paths": [str(path) for path in layer.paths],
                        "auxiliary_paths": [str(path) for path in layer.auxiliary_paths],
                    }
                    for layer in source_layers
                ],
                "provenance": {
                    "source": product.name,
                    "data_policy_url": product.license.identifier,
                    "license": product.license.identifier,
                    "attribution": product.license.attribution_text,
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                    "uncertainty_summary": (
                        None
                        if prepared.acquired is None
                        else _uncertainty_summary(prepared.acquired)
                    ),
                },
                "elevation_paths": (
                    []
                    if prepared.acquired is None
                    else [str(path) for path in prepared.acquired.paths]
                ),
                "imagery_paths": (
                    [] if imagery is None else [str(tile.path) for tile in imagery.tiles]
                ),
                "imagery": (
                    []
                    if imagery is None
                    else [
                        {
                            "path": str(tile.path),
                            "bounds": asdict(tile.bounds),
                            "width": tile.width,
                            "height": tile.height,
                            "gsd_metres": tile.gsd_metres,
                        }
                        for tile in imagery.tiles
                    ]
                ),
                "processed_elevation": processed_payload,
                "terrain_source": terrain_source_payload,
                "bathymetry": bathymetry_payload,
                "cache_reuse": {
                    "elevation_files": (
                        0 if prepared.acquired is None else prepared.acquired.cached_count
                    ),
                    "imagery_files": 0 if imagery is None else imagery.acquired.cached_count,
                    "bathymetry_files": (
                        0 if bathymetry is None else bathymetry.acquired.cached_count
                    ),
                },
            },
        )
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


def _uncertainty_summary(acquired: AcquiredRasterLayer) -> dict[str, object] | None:
    path = next(
        (path for path in acquired.auxiliary_paths if path.name == "uncertainty.json"),
        None,
    )
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
