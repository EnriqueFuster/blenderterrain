"""Execute a discovery job without accessing Blender data or bpy."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ..catalog import load_bundled_catalog
from ..catalog.models import Catalog, DatasetKind
from ..catalog.selection import (
    AcquisitionPlan,
    AcquisitionRequest,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
)
from ..core import (
    BBoxWGS84,
    ImportPlan,
    ProcessedBathymetryTile,
    ProcessedElevationTile,
    ProcessedImageryTile,
    RegionOfInterest,
    TransferProgress,
    compose_terrain_bathymetry,
    create_import_plan,
    geographic_source_bounds,
    process_elevation_tiles,
    process_gebco_tiles,
    process_worldcover_imagery,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer, acquire_plan_layers
from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    JobFormatError,
    NoCoverageError,
    ProviderUnavailableError,
    RasterFormatError,
    UserInputError,
)
from ..io.png_validation import validate_png
from ..models import DatasetProduct, ProjectedBounds
from ..providers.pnoa_planning import plan_pnoa_tiles
from ..providers.pnoa_wms import PNOAWMSClient
from ..providers.registry import build_raster_acquirers
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


@dataclass(frozen=True, slots=True)
class PreparedElevation:
    """Acquired sources and processed terrain from one confirmed elevation selection."""

    acquired: AcquiredRasterLayer | None
    import_plan: ImportPlan
    tiles: tuple[ProcessedElevationTile, ...]


@dataclass(frozen=True, slots=True)
class PreparedImagery:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedImageryTile, ...]


@dataclass(frozen=True, slots=True)
class PreparedBathymetry:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedBathymetryTile, ...]


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


def acquire_confirmed_sources(
    plan: AcquisitionPlan,
    cache_directory: Path,
    progress_callback: Callable[[TransferProgress], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    source_roi: BBoxWGS84 | None = None,
) -> tuple[AcquiredRasterLayer, ...]:
    """Execute the exact per-layer providers confirmed before worker startup."""

    provider_ids = tuple(selection.provider_id for selection in plan.selections.selections)
    return acquire_plan_layers(
        plan,
        acquirer_factory(provider_ids),
        cache_directory,
        progress_callback,
        cancellation_requested,
        source_roi,
    )


def prepare_confirmed_elevation(
    plan: AcquisitionPlan,
    catalog: Catalog,
    cache_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    maximum_elevation_samples: int = 16_777_216,
    maximum_imagery_pixels: int = 67_108_864,
    region: RegionOfInterest | None = None,
    manual_tile_rows: int | None = None,
    manual_tile_columns: int | None = None,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    elevation_processor: ElevationProcessor = process_elevation_tiles,
) -> PreparedElevation:
    """Acquire and process the single elevation layer confirmed for a terrain."""

    elevation_selections = tuple(
        selection
        for selection in plan.selections.selections
        if selection.kind in {DatasetKind.DTM, DatasetKind.DSM}
    )
    if len(elevation_selections) != 1:
        raise UserInputError("Terrain creation requires exactly one DTM or DSM selection")
    selection = elevation_selections[0]
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not selection.kind
    ):
        raise UserInputError("Selected elevation product does not match the catalog")
    layer_request = plan.request.layer(selection.kind)
    imagery_selection = plan.selections.for_kind(DatasetKind.IMAGERY)
    imagery_request = None if imagery_selection is None else plan.request.layer(DatasetKind.IMAGERY)
    import_plan = create_import_plan(
        plan.request.roi,
        product.id,
        layer_request.target_resolution_m,
        imagery_selection is not None,
        None if imagery_request is None else imagery_request.target_resolution_m,
        manual_tile_rows,
        manual_tile_columns,
        maximum_elevation_samples=maximum_elevation_samples,
        maximum_imagery_pixels=maximum_imagery_pixels,
        native_resolution_override=product.capabilities.native_resolution_m,
        use_global_utm=product.jurisdiction == "global",
        working_crs_epsg=None if product.wms is None else product.wms.crs_epsg,
    )
    resolved_request = LayerRequest(
        layer_request.kind,
        import_plan.elevation_resolution_metres,
        layer_request.temporal_policy,
    )
    elevation_plan = AcquisitionPlan(
        AcquisitionRequest(
            plan.request.roi,
            (resolved_request,),
            plan.request.license_profile,
        ),
        SelectionBundle((selection,)),
    )
    source_roi = geographic_source_bounds(import_plan) if product.jurisdiction == "global" else None
    try:
        acquired = acquire_confirmed_sources(
            elevation_plan,
            cache_directory,
            transfer_callback,
            cancellation_requested,
            acquirer_factory,
            source_roi,
        )[0]
    except NoCoverageError:
        if plan.selections.for_kind(DatasetKind.BATHYMETRY) is None:
            raise
        return PreparedElevation(None, import_plan, _empty_elevation_tiles(import_plan))

    def report_processing(completed: int, total: int) -> None:
        if cancellation_requested():
            raise JobCancelled("Elevation processing was cancelled")
        if processing_callback is not None:
            processing_callback(completed, total)

    if cancellation_requested():
        raise JobCancelled("Elevation processing was cancelled")
    tiles = elevation_processor(
        acquired.paths,
        import_plan,
        report_processing,
        region=region,
    )
    return PreparedElevation(acquired, import_plan, tiles)


def _empty_elevation_tiles(plan: ImportPlan) -> tuple[ProcessedElevationTile, ...]:
    """Create only the target grids needed for confirmed bathymetry output."""

    nodata = -32768.0
    return tuple(
        ProcessedElevationTile(
            zone_index,
            tile,
            np.full((tile.rows + 1, tile.columns + 1), nodata, np.float32),
            nodata,
            0,
            0,
            0.0,
        )
        for zone_index in range(len(plan.grids))
        for tile in plan.tiles_for_grid(zone_index)
    )


def prepare_confirmed_imagery(
    plan: AcquisitionPlan,
    catalog: Catalog,
    import_plan: ImportPlan,
    cache_directory: Path,
    output_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    pnoa_factory: Callable[[], PNOAWMSClient] = PNOAWMSClient,
) -> PreparedImagery | None:
    """Acquire and project the explicitly confirmed imagery selection, if any."""

    selection = plan.selections.for_kind(DatasetKind.IMAGERY)
    if selection is None:
        return None
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not DatasetKind.IMAGERY
    ):
        raise UserInputError("Selected imagery product is not supported by this worker")
    request = plan.request.layer(DatasetKind.IMAGERY)
    if product.id == DatasetProduct.PNOA_MA.value:
        return _prepare_pnoa_imagery(
            selection,
            request,
            import_plan,
            output_directory,
            transfer_callback,
            processing_callback,
            cancellation_requested,
            pnoa_factory(),
        )
    if product.id not in {"ESA_WORLDCOVER_S2_2021", "FR_BD_ORTHO"}:
        raise UserInputError("Selected imagery product is not supported by this worker")
    imagery_plan = AcquisitionPlan(
        AcquisitionRequest(plan.request.roi, (request,), plan.request.license_profile),
        SelectionBundle((selection,)),
    )
    acquired = acquire_confirmed_sources(
        imagery_plan,
        cache_directory,
        transfer_callback,
        cancellation_requested,
        acquirer_factory,
        geographic_source_bounds(import_plan),
    )[0]
    if product.id == "FR_BD_ORTHO":
        return PreparedImagery(acquired, _projected_wms_imagery_tiles(acquired))
    tiles = process_worldcover_imagery(
        acquired.paths,
        import_plan,
        output_directory,
        processing_callback,
        cancellation_requested,
    )
    return PreparedImagery(acquired, tiles)


def _projected_wms_imagery_tiles(
    acquired: AcquiredRasterLayer,
) -> tuple[ProcessedImageryTile, ...]:
    """Read georeferencing written beside already-projected WMS images."""

    sidecars = {path.with_suffix("").name: path for path in acquired.auxiliary_paths}
    outputs: list[ProcessedImageryTile] = []
    for path in acquired.paths:
        sidecar = sidecars.get(path.name)
        if sidecar is None:
            raise RasterFormatError("Projected WMS imagery provenance is missing")
        try:
            payload = json.loads(sidecar.read_text(encoding="utf-8"))
            bbox = payload["bbox"]
            width = int(payload["width"])
            height = int(payload["height"])
            bounds = ProjectedBounds(
                float(bbox[0]),
                float(bbox[1]),
                float(bbox[2]),
                float(bbox[3]),
                int(payload["crs_epsg"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RasterFormatError("Projected WMS imagery provenance is invalid") from exc
        validate_png(path, width, height)
        outputs.append(
            ProcessedImageryTile(
                path,
                bounds,
                width,
                height,
                (bounds.east - bounds.west) / width,
            )
        )
    return tuple(outputs)


def _prepare_pnoa_imagery(
    selection: ProductSelection,
    request: LayerRequest,
    import_plan: ImportPlan,
    output_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None,
    processing_callback: Callable[[int, int], None] | None,
    cancellation_requested: Callable[[], bool],
    client: PNOAWMSClient,
) -> PreparedImagery:
    """Download already-projected PNOA tiles for one confirmed imagery selection."""

    if request.kind is not DatasetKind.IMAGERY:
        raise UserInputError("PNOA requires an imagery layer request")
    requests = plan_pnoa_tiles(import_plan)
    output_directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    cached_count = 0
    if processing_callback is not None:
        processing_callback(0, len(requests))
    for index, tile in enumerate(requests):
        if cancellation_requested():
            raise JobCancelled("Imagery acquisition was cancelled")
        path = output_directory / tile.filename
        if path.is_file():
            validate_png(path, tile.width, tile.height)
            cached_count += 1
            if transfer_callback is not None:
                size = path.stat().st_size
                transfer_callback(
                    TransferProgress(
                        DatasetKind.IMAGERY.value,
                        index,
                        len(requests),
                        tile.filename,
                        size,
                        size,
                        cached=True,
                    )
                )
        else:

            def report_download(
                written: int,
                expected: int | None,
                *,
                file_index: int = index,
                filename: str = tile.filename,
            ) -> None:
                if transfer_callback is not None:
                    transfer_callback(
                        TransferProgress(
                            DatasetKind.IMAGERY.value,
                            file_index,
                            len(requests),
                            filename,
                            written,
                            expected,
                        )
                    )

            path = client.download_png(
                tile.bounds,
                tile.width,
                tile.height,
                output_directory,
                tile.filename,
                progress_callback=(None if transfer_callback is None else report_download),
            )
        paths.append(path)
        if processing_callback is not None:
            processing_callback(len(paths), len(requests))
    acquired = AcquiredRasterLayer(
        selection.provider_id,
        selection.product_id,
        DatasetKind.IMAGERY,
        tuple(paths),
        cached_count,
    )
    tiles = tuple(
        ProcessedImageryTile(path, tile.bounds, tile.width, tile.height, tile.gsd_metres)
        for path, tile in zip(paths, requests, strict=True)
    )
    return PreparedImagery(acquired, tiles)


def prepare_confirmed_bathymetry(
    plan: AcquisitionPlan,
    catalog: Catalog,
    import_plan: ImportPlan,
    cache_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None = None,
    processing_callback: Callable[[int, int], None] | None = None,
    cancellation_requested: Callable[[], bool] = lambda: False,
    acquirer_factory: Callable[[tuple[str, ...]], dict[str, RasterAcquirer]] = (
        build_raster_acquirers
    ),
    region: RegionOfInterest | None = None,
) -> PreparedBathymetry | None:
    """Acquire and align the explicitly confirmed bathymetry selection, if any."""

    selection = plan.selections.for_kind(DatasetKind.BATHYMETRY)
    if selection is None:
        return None
    product = catalog.product(selection.product_id)
    if (
        product.provider_id != selection.provider_id
        or product.capabilities.kind is not DatasetKind.BATHYMETRY
        or product.id != "GEBCO_2026"
    ):
        raise UserInputError("Selected bathymetry product is not supported by this worker")
    request = plan.request.layer(DatasetKind.BATHYMETRY)
    bathymetry_plan = AcquisitionPlan(
        AcquisitionRequest(plan.request.roi, (request,), plan.request.license_profile),
        SelectionBundle((selection,)),
    )
    acquired = acquire_confirmed_sources(
        bathymetry_plan,
        cache_directory,
        transfer_callback,
        cancellation_requested,
        acquirer_factory,
        geographic_source_bounds(import_plan),
    )[0]
    tid_path = next((path for path in acquired.auxiliary_paths if path.name == "tid.npy"), None)
    if tid_path is None or len(acquired.paths) != 1:
        raise RasterFormatError("GEBCO elevation and TID windows are required")
    tiles = process_gebco_tiles(
        acquired.paths[0],
        tid_path,
        import_plan,
        processing_callback,
        region,
    )
    return PreparedBathymetry(acquired, tiles)
