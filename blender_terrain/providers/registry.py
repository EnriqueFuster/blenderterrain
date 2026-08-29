"""Construction of acquisition adapters implemented by this installation."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.acquisition import RasterAcquirer
from .cnig_acquisition import CnigElevationAcquirer
from .copernicus_dem import CopernicusGlo30Acquirer
from .gedtm30 import Gedtm30Acquirer
from .worldcover import WorldCoverAcquirer


def build_raster_acquirers(provider_ids: Iterable[str]) -> dict[str, RasterAcquirer]:
    """Build only the adapters referenced by a confirmed acquisition plan."""

    requested = set(provider_ids)
    factories = {
        "ign_cnig": CnigElevationAcquirer,
        "copernicus_dem": CopernicusGlo30Acquirer,
        "openlandmap": Gedtm30Acquirer,
        "esa_worldcover": WorldCoverAcquirer,
    }
    return {
        provider_id: factories[provider_id]()
        for provider_id in sorted(requested)
        if provider_id in factories
    }
