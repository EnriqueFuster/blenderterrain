"""Construction of acquisition adapters implemented by this installation."""

from __future__ import annotations

from collections.abc import Iterable

from ..catalog.models import ProductRecord
from ..core.acquisition import RasterAcquirer
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
