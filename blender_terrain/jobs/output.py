"""Shared formatting and persistence for prepared job outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from ..core import ProcessedElevationTile, TransferProgress
from ..errors import JobCancelled
from ..io.elevation_output import write_elevation_array


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
