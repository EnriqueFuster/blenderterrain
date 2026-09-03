"""Adapt existing CNIG elevation delivery to provider-neutral acquisition."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from ..catalog import load_bundled_catalog
from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.planning import create_import_plan
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled
from ..io.tiff_validation import validate_tiff_header
from ..models import CatalogItem, CatalogPage, DatasetProduct
from .cnig_discovery import discover_sources
from .cnig_portal import CNIGPortalClient
from .spain_crs import split_spain_bbox_by_utm_zone


class _CnigClient(Protocol):
    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage: ...

    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
        progress_callback: Callable[[int, int | None], None] | None = None,
    ) -> Path: ...


class CnigElevationAcquirer:
    """Acquire one confirmed IGN elevation product through the CNIG portal."""

    def __init__(
        self,
        client: _CnigClient | None = None,
        maximum_file_bytes: int = 1_073_741_824,
    ) -> None:
        if maximum_file_bytes <= 0:
            raise ValueError("Maximum file size must be positive")
        self.client = client or CNIGPortalClient()
        self.maximum_file_bytes = maximum_file_bytes

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        if selection.provider_id != "ign_cnig" or selection.kind not in {
            DatasetKind.DTM,
            DatasetKind.DSM,
        } or request.kind is not selection.kind:
            raise ValueError("CNIG acquirer received an incompatible selection")
        try:
            product = DatasetProduct(selection.product_id)
        except ValueError as exc:
            raise ValueError("CNIG selection contains an unknown elevation product") from exc
        plan = create_import_plan(
            roi,
            product,
            request.target_resolution_m,
            False,
            None,
            native_resolution_override=(
                load_bundled_catalog()
                .product(selection.product_id)
                .capabilities.native_resolution_m
            ),
            work_areas_override=split_spain_bbox_by_utm_zone(roi),
        )
        discovery = discover_sources(plan, self.client)
        target = cache_directory / selection.provider_id / selection.product_id
        paths: list[Path] = []
        cached_count = 0
        for index, item in enumerate(discovery.items):
            if cancellation_requested():
                raise JobCancelled("CNIG acquisition was cancelled")
            destination = target / item.filename
            if destination.is_file():
                validate_tiff_header(destination)
                paths.append(destination)
                cached_count += 1
                if progress_callback is not None:
                    size = destination.stat().st_size
                    progress_callback(
                        TransferProgress(
                            selection.kind.value,
                            index,
                            len(discovery.items),
                            item.filename,
                            size,
                            size,
                            cached=True,
                        )
                    )
                continue

            def report(
                written: int,
                expected: int | None,
                *,
                _index: int = index,
                _filename: str = item.filename,
            ) -> None:
                if progress_callback is not None:
                    progress_callback(
                        TransferProgress(
                            selection.kind.value,
                            _index,
                            len(discovery.items),
                            _filename,
                            written,
                            expected,
                        )
                    )

            paths.append(
                self.client.download_item(
                    item,
                    target,
                    maximum_bytes=self.maximum_file_bytes,
                    progress_callback=report,
                )
            )
        return AcquiredRasterLayer(
            selection.provider_id,
            selection.product_id,
            selection.kind,
            tuple(paths),
            cached_count,
        )
