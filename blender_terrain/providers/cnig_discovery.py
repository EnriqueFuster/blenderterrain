"""Select CNIG catalog files required by a legacy import plan."""

from __future__ import annotations

from typing import Protocol

from ..core.planning import ImportPlan
from ..core.roi import BBoxWGS84
from ..errors import CatalogContractChanged, NoCoverageError
from ..models import CatalogItem, CatalogPage, DatasetProduct, DiscoveryResult


class CatalogDiscoveryProvider(Protocol):
    """CNIG catalog capability required for source discovery."""

    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        """Return every catalog page for a product and ROI."""


def select_catalog_items(plan: ImportPlan, catalog: CatalogPage) -> DiscoveryResult:
    """Select matching CNIG native UTM files for an import plan."""

    expected_zones = {area.crs.utm_zone for area in plan.work_areas}
    selected: list[CatalogItem] = []
    seen_ids: set[str] = set()
    for item in catalog.items:
        if item.product != plan.product or not item.is_native_projected_variant:
            continue
        zone = item.native_utm_zone
        if zone is None:
            raise CatalogContractChanged(
                f"Native catalog filename does not identify a UTM zone: {item.filename}"
            )
        if zone not in expected_zones:
            continue
        if item.sequential_id in seen_ids:
            continue
        seen_ids.add(item.sequential_id)
        selected.append(item)
    if not selected:
        raise NoCoverageError("CNIG catalog returned no native UTM elevation files for the ROI")
    selected.sort(key=lambda item: (item.native_utm_zone or 0, item.filename, item.sequential_id))
    return DiscoveryResult(
        items=tuple(selected),
        advertised_items=catalog.total_items,
        ignored_items=len(catalog.items) - len(selected),
    )


def discover_sources(
    plan: ImportPlan, provider: CatalogDiscoveryProvider
) -> DiscoveryResult:
    """Discover CNIG rows and reduce them to sources required by the plan."""

    if not isinstance(plan.product, DatasetProduct):
        raise ValueError("CNIG catalog discovery requires a CNIG product")
    catalog = provider.discover_all(plan.product, plan.bounds)
    return select_catalog_items(plan, catalog)
