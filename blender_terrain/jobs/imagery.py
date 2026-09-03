"""Prepare imagery selected by a confirmed acquisition plan."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..catalog.models import Catalog, DatasetKind
from ..catalog.selection import (
    AcquisitionPlan,
    AcquisitionRequest,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
)
from ..core import (
    ImportPlan,
    ProcessedImageryTile,
    TransferProgress,
    geographic_source_bounds,
    process_worldcover_imagery,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer, acquire_plan_layers
from ..errors import JobCancelled, RasterFormatError, UserInputError
from ..io.png_validation import validate_png
from ..models import DatasetProduct, ProjectedBounds
from ..providers.pnoa_planning import plan_pnoa_tiles
from ..providers.pnoa_wms import PNOAWMSClient
from ..providers.registry import build_raster_acquirers


@dataclass(frozen=True, slots=True)
class PreparedImagery:
    acquired: AcquiredRasterLayer
    tiles: tuple[ProcessedImageryTile, ...]


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
    provider_ids = tuple(item.provider_id for item in imagery_plan.selections.selections)
    acquired = acquire_plan_layers(
        imagery_plan,
        acquirer_factory(provider_ids),
        cache_directory,
        transfer_callback,
        cancellation_requested,
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
