"""Export prepared cache artifacts as interoperable georeferenced rasters."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..errors import RasterFormatError
from ..io.atomic import finalize_part
from ..io.geotiff import write_geotiff
from ..io.png_validation import read_rgb_png
from ..models import ProjectedBounds


@dataclass(frozen=True, slots=True)
class PreparedRasterExport:
    """Completed export directory and the files written into it."""

    directory: Path
    paths: tuple[Path, ...]


def export_prepared_rasters(
    result_path: Path,
    destination: Path,
    progress_callback: Callable[[float, str], None] | None = None,
) -> PreparedRasterExport:
    """Export final elevation, marine masks, and imagery from one delivery result."""

    result = _read_result(result_path)
    import_id = result.get("import_id")
    if not isinstance(import_id, str) or not import_id:
        raise RasterFormatError("Delivery result has no import identifier")
    output_directory = _available_directory(destination, f"BlenderTerrain_{import_id[:8]}_prepared")
    tasks = _export_tasks(result)
    if not tasks:
        raise RasterFormatError("Delivery result contains no prepared rasters")
    output_directory.mkdir(parents=True)
    written: list[Path] = []
    try:
        for index, task in enumerate(tasks):
            kind, source, bounds, nodata, pixel_is_point = task
            output = output_directory / f"{index + 1:03d}_{kind}_{source.stem}.tif"
            _report(progress_callback, index / (len(tasks) + 1), f"Writing {output.name}")
            data = read_rgb_png(source) if source.suffix.lower() == ".png" else np.load(
                source, mmap_mode="r", allow_pickle=False
            )

            def file_progress(
                fraction: float,
                task_index: int = index,
                filename: str = output.name,
            ) -> None:
                _report(
                    progress_callback,
                    (task_index + fraction) / (len(tasks) + 1),
                    f"Writing {filename}",
                )

            write_geotiff(
                output,
                data,
                bounds,
                nodata=nodata,
                pixel_is_point=pixel_is_point,
                progress_callback=None if progress_callback is None else file_progress,
            )
            written.append(output)
        provenance_path = output_directory / "provenance.json"
        provenance_part = output_directory / "provenance.json.part"
        _report(
            progress_callback,
            len(tasks) / (len(tasks) + 1),
            "Writing provenance.json",
        )
        provenance_part.write_text(
            json.dumps(
                {
                    "schema_version": result.get("schema_version"),
                    "import_id": import_id,
                    "request": result.get("request"),
                    "crs": result.get("crs"),
                    "sources": result.get("sources"),
                    "provenance": result.get("provenance"),
                    "warnings": result.get("warnings", []),
                    "exported_files": [path.name for path in written],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        finalize_part(provenance_part, provenance_path)
        written.append(provenance_path)
        _report(progress_callback, 1.0, "Prepared raster export complete")
        return PreparedRasterExport(output_directory, tuple(written))
    except BaseException:
        (output_directory / "provenance.json.part").unlink(missing_ok=True)
        for path in written:
            path.unlink(missing_ok=True)
        if output_directory.exists() and not any(output_directory.iterdir()):
            output_directory.rmdir()
        raise


ExportTask = tuple[str, Path, ProjectedBounds, float | int | None, bool]


def _export_tasks(result: dict[str, object]) -> tuple[ExportTask, ...]:
    tasks: list[ExportTask] = []
    elevation = result.get("processed_elevation", [])
    if not isinstance(elevation, list):
        raise RasterFormatError("Delivery result has invalid prepared elevation entries")
    for entry in elevation:
        if not isinstance(entry, dict):
            raise RasterFormatError("Delivery result has an invalid elevation entry")
        bounds = _bounds(entry.get("bounds"))
        tasks.append(
            (
                "elevation",
                _existing_path(entry.get("path")),
                bounds,
                _number(entry.get("nodata")),
                True,
            )
        )
        mask_path = entry.get("marine_mask_path")
        if isinstance(mask_path, str):
            tasks.append(("marine_mask", _existing_path(mask_path), bounds, None, True))
    imagery = result.get("imagery", [])
    if not isinstance(imagery, list):
        raise RasterFormatError("Delivery result has invalid prepared imagery entries")
    for entry in imagery:
        if not isinstance(entry, dict):
            raise RasterFormatError("Delivery result has an invalid imagery entry")
        tasks.append(
            (
                "imagery",
                _existing_path(entry.get("path")),
                _bounds(entry.get("bounds")),
                None,
                False,
            )
        )
    return tuple(tasks)


def _read_result(path: Path) -> dict[str, object]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RasterFormatError("Delivery result cannot be read") from exc
    if not isinstance(result, dict):
        raise RasterFormatError("Delivery result must be a JSON object")
    return result


def _bounds(value: object) -> ProjectedBounds:
    if not isinstance(value, dict):
        raise RasterFormatError("Prepared raster has no projected bounds")
    try:
        return ProjectedBounds(
            float(value["west"]),
            float(value["south"]),
            float(value["east"]),
            float(value["north"]),
            int(value["epsg"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Prepared raster bounds are invalid") from exc


def _existing_path(value: object) -> Path:
    if not isinstance(value, str):
        raise RasterFormatError("Prepared raster path is invalid")
    path = Path(value)
    if not path.is_file():
        raise RasterFormatError(f"Prepared raster is missing: {path.name}")
    return path


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RasterFormatError("Prepared raster NoData value is invalid")
    return float(value)


def _available_directory(parent: Path, name: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    candidate = parent / name
    suffix = 2
    while candidate.exists():
        candidate = parent / f"{name}_{suffix}"
        suffix += 1
    return candidate


def _report(
    callback: Callable[[float, str], None] | None, progress: float, message: str
) -> None:
    if callback is not None:
        callback(progress, message)
