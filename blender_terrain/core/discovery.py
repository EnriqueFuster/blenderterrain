"""Select provider catalog rows required by a validated import plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import CatalogContractChanged, NoCoverageError
from ..models import CatalogItem, CatalogPage, DatasetProduct
from .planning import ImportPlan
from .roi import BBoxWGS84


class CatalogDiscoveryProvider(Protocol):
    """Provider capability required by portable discovery orchestration."""

    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        """Return every catalog page for a product and ROI."""


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Native projected source files selected for one import plan."""

    items: tuple[CatalogItem, ...]
    advertised_items: int
    ignored_items: int

    @property
    def estimated_download_mb(self) -> float | None:
        """Sum advertised sizes, or return None if any size is unknown."""

        if any(item.size_mb is None for item in self.items):
            return None
        return sum(item.size_mb or 0.0 for item in self.items)


def select_catalog_items(plan: ImportPlan, catalog: CatalogPage) -> DiscoveryResult:
    """Filter native UTM rows and reject ambiguous or missing zone metadata."""

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
    """Discover provider rows and reduce them to sources required by the plan."""

    if not isinstance(plan.product, DatasetProduct):
        raise ValueError("Legacy catalog discovery requires a CNIG product")
    catalog = provider.discover_all(plan.product, plan.bounds)
    return select_catalog_items(plan, catalog)
