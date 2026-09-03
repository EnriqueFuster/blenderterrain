"""Shared formatting and persistence for prepared job outputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..catalog.models import DatasetKind
from ..core import (
    ComposedTerrainTile,
    ProcessedBathymetryTile,
    ProcessedElevationTile,
    TransferProgress,
)
from ..errors import JobCancelled
from ..io.elevation_output import write_elevation_array, write_quality_array
from .models import RESULT_SCHEMA_VERSION, JobState

if TYPE_CHECKING:
    from ..catalog.models import Catalog
    from ..core.acquisition import AcquiredRasterLayer
    from .acquisition_job import AcquisitionJob
    from .imagery import PreparedImagery
    from .terrain import PreparedBathymetry, PreparedElevation


def transfer_message(transfer: TransferProgress) -> str:
    """Format one provider-neutral transfer progress message."""

    kind = {
        "elevation": "elevation",
        "imagery": "imagery",
        "dtm": "DTM",
        "dsm": "DSM",
        "bathymetry": "GEBCO bathymetry",
    }.get(transfer.kind, transfer.kind)
    position = f"{transfer.file_index + 1}/{transfer.file_count}"
    if transfer.cached:
        return f"Using cached {kind} {position}: {transfer.filename}"
    written_mib = transfer.written_bytes / 1_048_576
    if transfer.expected_bytes:
        expected_mib = transfer.expected_bytes / 1_048_576
        percentage = min(100.0, transfer.written_bytes / transfer.expected_bytes * 100.0)
        size = f"{written_mib:.1f}/{expected_mib:.1f} MiB, {percentage:.0f}%"
    else:
        size = f"{written_mib:.1f} MiB"
    return f"Downloading {kind} {position}: {transfer.filename} ({size})"


def build_result_payload(
    job: AcquisitionJob,
    catalog: Catalog,
    elevation: PreparedElevation,
    imagery: PreparedImagery | None,
    bathymetry: PreparedBathymetry | None,
    processed_elevation: list[dict[str, object]],
    terrain_source: list[dict[str, object]] | None,
    processed_bathymetry: list[dict[str, object]],
    imagery_unavailable: bool,
) -> tuple[JobState, dict[str, Any]]:
    """Build the versioned result for one completed acquisition job."""

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
        ([] if elevation.acquired is None else [elevation.acquired])
        + ([] if bathymetry is None else [bathymetry.acquired])
        + ([] if imagery is None else [imagery.acquired])
    )
    elevation_unavailable = elevation.acquired is None
    final_state = (
        JobState.COMPLETE_WITH_WARNINGS
        if elevation_unavailable or imagery_unavailable
        else JobState.COMPLETE
    )
    payload = {
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
            "elevation_resolution_metres": elevation.import_plan.elevation_resolution_metres,
            "use_imagery": imagery is not None,
            "use_bathymetry": bathymetry is not None,
            "imagery_gsd_metres": (
                None
                if elevation.import_plan.imagery is None
                else elevation.import_plan.imagery.gsd_metres
            ),
            "manual_tile_rows": job.manual_tile_rows,
            "manual_tile_columns": job.manual_tile_columns,
        },
        "crs": [asdict(area.crs) for area in elevation.import_plan.work_areas],
        "sources": [_source_payload(layer, catalog) for layer in source_layers],
        "provenance": {
            "source": product.name,
            "data_policy_url": product.license.identifier,
            "license": product.license.identifier,
            "attribution": product.license.attribution_text,
            "retrieved_at_utc": datetime.now(UTC).isoformat(),
            "uncertainty_summary": (
                None if elevation.acquired is None else _uncertainty_summary(elevation.acquired)
            ),
        },
        "elevation_paths": (
            [] if elevation.acquired is None else [str(path) for path in elevation.acquired.paths]
        ),
        "imagery_paths": [] if imagery is None else [str(tile.path) for tile in imagery.tiles],
        "imagery": []
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
        ],
        "processed_elevation": processed_elevation,
        "terrain_source": terrain_source,
        "bathymetry": processed_bathymetry,
        "cache_reuse": {
            "elevation_files": 0 if elevation.acquired is None else elevation.acquired.cached_count,
            "imagery_files": 0 if imagery is None else imagery.acquired.cached_count,
            "bathymetry_files": 0 if bathymetry is None else bathymetry.acquired.cached_count,
        },
    }
    return final_state, payload


def _source_payload(layer: AcquiredRasterLayer, catalog: Catalog) -> dict[str, object]:
    product = catalog.product(layer.product_id)
    return {
        "provider_id": layer.provider_id,
        "product_id": layer.product_id,
        "kind": layer.kind.value,
        "license": product.license.identifier,
        "attribution": product.license.attribution_text,
        "paths": [str(path) for path in layer.paths],
        "auxiliary_paths": [str(path) for path in layer.auxiliary_paths],
    }


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


def write_processed_tiles(
    tiles: tuple[ProcessedElevationTile, ...],
    directory: Path,
    cancellation_requested: Callable[[], bool],
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, object]]:
    """Persist processed elevation arrays and return their result records."""

    payload: list[dict[str, object]] = []
    for processed in tiles:
        if cancellation_requested():
            raise JobCancelled("Elevation output writing was cancelled")
        filename = (
            f"elevation_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
            f"r{processed.tile.row}_c{processed.tile.column}.npy"
        )
        output_path = directory / filename
        write_elevation_array(output_path, processed.data)
        payload.append(
            {
                "path": str(output_path),
                "bounds": asdict(processed.tile.bounds),
                "rows": processed.tile.rows,
                "columns": processed.tile.columns,
                "nodata": processed.nodata,
                "overlap_valid_pixels": processed.overlap_valid_pixels,
                "conflicting_valid_pixels": processed.conflicting_valid_pixels,
                "maximum_overlap_difference": processed.maximum_overlap_difference,
            }
        )
        if progress_callback is not None:
            progress_callback(len(payload), len(tiles))
    return payload


def write_processed_bathymetry(
    tiles: tuple[ProcessedBathymetryTile, ...],
    directory: Path,
    cancellation_requested: Callable[[], bool],
) -> list[dict[str, object]]:
    """Persist prepared bathymetry and quality arrays."""

    payload: list[dict[str, object]] = []
    for processed in tiles:
        if cancellation_requested():
            raise JobCancelled("Bathymetry output writing was cancelled")
        stem = (
            f"bathymetry_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
            f"r{processed.tile.row}_c{processed.tile.column}"
        )
        elevation_path = directory / f"{stem}.npy"
        tid_path = directory / f"{stem}_tid.npy"
        write_elevation_array(elevation_path, processed.elevation)
        write_quality_array(tid_path, processed.tid)
        payload.append(
            {
                "path": str(elevation_path),
                "tid_path": str(tid_path),
                "bounds": asdict(processed.tile.bounds),
                "rows": processed.tile.rows,
                "columns": processed.tile.columns,
                "nodata": processed.nodata,
            }
        )
    return payload


def write_composed_tiles(
    tiles: tuple[ComposedTerrainTile, ...],
    directory: Path,
    cancellation_requested: Callable[[], bool],
) -> list[dict[str, object]]:
    """Persist terrain combined with bathymetry and its marine mask."""

    payload: list[dict[str, object]] = []
    for processed in tiles:
        if cancellation_requested():
            raise JobCancelled("Composed terrain output writing was cancelled")
        stem = (
            f"elevation_epsg{processed.tile.bounds.epsg}_z{processed.zone_index}_"
            f"r{processed.tile.row}_c{processed.tile.column}"
        )
        elevation_path = directory / f"{stem}.npy"
        mask_path = directory / f"{stem}_marine_mask.npy"
        write_elevation_array(elevation_path, processed.elevation)
        write_quality_array(mask_path, processed.marine_mask)
        payload.append(
            {
                "path": str(elevation_path),
                "marine_mask_path": str(mask_path),
                "bounds": asdict(processed.tile.bounds),
                "rows": processed.tile.rows,
                "columns": processed.tile.columns,
                "nodata": processed.nodata,
            }
        )
    return payload
