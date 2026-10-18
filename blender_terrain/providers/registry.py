"""Construction of acquisition adapters implemented by this installation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Protocol

from ..catalog.models import ProductRecord
from ..catalog.selection import LayerRequest, ProductSelection
from ..core import (
    ImportPlan,
    ProcessedBathymetryTile,
    ProcessedImageryTile,
    RegionOfInterest,
    TransferProgress,
)
from ..core.acquisition import AcquiredRasterLayer, RasterAcquirer
from ..core.crs import ProjectedWorkArea
from ..core.roi import BBoxWGS84
from .cnig_acquisition import CnigElevationAcquirer
from .copernicus_dem import CopernicusGlo30Acquirer
from .datamap_wales import DataMapWalesAcquirer
from .environment_agency import EnvironmentAgencyWCSAcquirer
from .gebco import GebcoAcquirer
from .gedtm30 import Gedtm30Acquirer
from .geopf_wms import GeopfWMSAcquirer
from .osni import OsniAcquirer
from .scottish_lidar import ScottishLidarAcquirer
from .sentinel2 import Sentinel2Acquirer
from .spain_crs import split_spain_bbox_by_utm_zone
from .worldcover import WorldCoverAcquirer


class DirectImageryPreparer(Protocol):
    """Prepare imagery whose provider already returns the destination grid."""

    def __call__(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        import_plan: ImportPlan,
        output_directory: Path,
        transfer_callback: Callable[[TransferProgress], None] | None,
        processing_callback: Callable[[int, int], None] | None,
        cancellation_requested: Callable[[], bool],
    ) -> tuple[AcquiredRasterLayer, tuple[ProcessedImageryTile, ...]]: ...


class ImageryProcessor(Protocol):
    """Project acquired imagery windows onto the terrain texture grid."""

    def __call__(
        self,
        source_paths: tuple[Path, ...],
        plan: ImportPlan,
        output_directory: Path,
        progress_callback: Callable[[int, int], None] | None,
        cancellation_requested: Callable[[], bool],
    ) -> tuple[ProcessedImageryTile, ...]: ...


class BathymetryProcessor(Protocol):
    """Validate and project one provider's bathymetry payload."""

    def __call__(
        self,
        acquired: AcquiredRasterLayer,
        plan: ImportPlan,
        progress_callback: Callable[[int, int], None] | None,
        region: RegionOfInterest | None,
    ) -> tuple[ProcessedBathymetryTile, ...]: ...


def build_raster_acquirers(provider_ids: Iterable[str]) -> dict[str, RasterAcquirer]:
    """Build only the adapters referenced by a confirmed acquisition plan."""

    requested = set(provider_ids)
    factories = {
        "ign_cnig": CnigElevationAcquirer,
        "copernicus_dem": CopernicusGlo30Acquirer,
        "openlandmap": Gedtm30Acquirer,
        "esa_worldcover": WorldCoverAcquirer,
        "gebco": GebcoAcquirer,
        "ign_france": GeopfWMSAcquirer,
        "environment_agency": EnvironmentAgencyWCSAcquirer,
        "datamap_wales": DataMapWalesAcquirer,
        "scottish_remote_sensing": ScottishLidarAcquirer,
        "osni": OsniAcquirer,
        "sentinel2": Sentinel2Acquirer,
    }
    return {
        provider_id: factories[provider_id]()
        for provider_id in sorted(requested)
        if provider_id in factories
    }


def projected_work_areas(
    product: ProductRecord, roi: BBoxWGS84
) -> tuple[ProjectedWorkArea, ...] | None:
    """Return provider-specific native work areas when a product requires them."""

    if product.provider_id == "ign_cnig":
        return split_spain_bbox_by_utm_zone(roi)
    return None


def direct_imagery_preparer(product_id: str) -> DirectImageryPreparer | None:
    """Return the provider implementation for directly projected imagery."""

    if product_id == "PNOA_MA":
        from .pnoa_imagery import prepare_pnoa_imagery

        return prepare_pnoa_imagery
    return None


def imagery_processor(product_id: str) -> ImageryProcessor | None:
    """Return the processing adapter declared by an imagery product."""

    if product_id == "ESA_WORLDCOVER_S2_2021":
        from .worldcover import process_worldcover_imagery

        return process_worldcover_imagery
    if product_id == "SENTINEL2_L2A":
        from .sentinel2 import process_sentinel2_imagery

        return process_sentinel2_imagery
    return None


def bathymetry_processor(product_id: str) -> BathymetryProcessor | None:
    """Return the processing adapter declared by a bathymetry product."""

    if product_id == "GEBCO_2026":
        from .gebco import process_gebco_layer

        return process_gebco_layer
    return None


def supports_catalog_availability(provider_id: str) -> bool:
    """Return whether a provider implements the legacy availability probe."""

    return provider_id == "ign_cnig"


def supports_imagery_discovery(product_id: str) -> bool:
    """Return whether an imagery product has a dynamic discovery worker."""

    return product_id == "SENTINEL2_L2A"


def imagery_temporal_policy(
    product: ProductRecord,
    start_date: str,
    end_date: str,
    maximum_cloud: int,
) -> str | None:
    """Build the optional temporal policy understood by an imagery provider."""

    if product.id == "SENTINEL2_L2A":
        from .sentinel2 import sentinel2_temporal_policy

        return sentinel2_temporal_policy(start_date, end_date, maximum_cloud)
    return product.version if not product.capabilities.temporal else None


def source_discovery_summary(
    product: ProductRecord, roi: BBoxWGS84
) -> tuple[int, str]:
    """Describe bounded source work without exposing provider cases to the UI."""

    if product.id == "COPERNICUS_GLO30_2021":
        from .copernicus_dem import glo30_tiles_for_roi

        count = len(glo30_tiles_for_roi(roi))
        return count, f"{count} {product.name} tile(s) required"
    if product.id == "GEDTM30_V11":
        return 2, f"{product.name} elevation and uncertainty windows required"
    protocol = product.capabilities.acquisition_mode.value.upper()
    return 0, (
        f"{product.name} {protocol} windows required; "
        "valid pixels are confirmed during download"
    )


def provider_job_runners() -> dict[str, Callable[[Path], object]]:
    """Return provider-owned legacy worker modes for the shared entry point."""

    from .cnig_jobs import run_cnig_availability_job, run_cnig_discovery_job
    from .sentinel2_jobs import run_sentinel2_discovery_job

    return {
        "provider_availability": run_cnig_availability_job,
        "provider_discovery": run_cnig_discovery_job,
        "imagery_discovery": run_sentinel2_discovery_job,
    }
