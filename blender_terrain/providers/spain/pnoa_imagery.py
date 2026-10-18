"""Acquire PNOA imagery on the terrain texture grid."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...catalog.models import DatasetKind
from ...catalog.selection import LayerRequest, ProductSelection
from ...core import ImportPlan, ProcessedImageryTile, TransferProgress
from ...core.acquisition import AcquiredRasterLayer
from ...errors import JobCancelled, UserInputError
from ...io.png_validation import validate_png
from .pnoa_planning import plan_pnoa_tiles
from .pnoa_wms import PNOAWMSClient


def prepare_pnoa_imagery(
    selection: ProductSelection,
    request: LayerRequest,
    import_plan: ImportPlan,
    output_directory: Path,
    transfer_callback: Callable[[TransferProgress], None] | None,
    processing_callback: Callable[[int, int], None] | None,
    cancellation_requested: Callable[[], bool],
    client: PNOAWMSClient | None = None,
) -> tuple[AcquiredRasterLayer, tuple[ProcessedImageryTile, ...]]:
    """Download one confirmed PNOA selection without leaking it into job code."""

    if request.kind is not DatasetKind.IMAGERY:
        raise UserInputError("PNOA requires an imagery layer request")
    requests = plan_pnoa_tiles(import_plan)
    output_directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    cached_count = 0
    service = client or PNOAWMSClient()
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

            path = service.download_png(
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
    return acquired, tiles
