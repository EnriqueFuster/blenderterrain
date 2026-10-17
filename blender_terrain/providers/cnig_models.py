"""Data models for the IGN-CNIG catalog and download contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class CnigProduct(StrEnum):
    """Products supported by the IGN-CNIG integration."""

    MDT50CM = "MDT50CM"
    MDT02 = "MDT02"
    MDT05 = "MDT05"
    MDT25 = "MDT25"
    MDT200 = "MDT200"
    MDS50CM = "MDS50CM"
    MDS02 = "MDS02"
    MDS05 = "MDS05"
    PNOA_MA = "PNOA_MA"


@dataclass(frozen=True, slots=True)
class CnigProductPage:
    """Dynamic fields extracted from an IGN-CNIG product page."""

    catalog_group: str
    catalog_series: str
    advertised_total: int
    attribution_ids: str
    formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CnigCatalogItem:
    """A downloadable resource advertised by the IGN-CNIG catalog."""

    product: CnigProduct
    filename: str
    file_format: str
    sequential_id: str
    date: str | None = None
    resolution: str | None = None
    size_mb: float | None = None

    @property
    def is_native_projected_variant(self) -> bool:
        """Return whether the filename identifies a supported native CRS variant."""

        upper_name = self.filename.upper()
        if "WGS84" in upper_name:
            return False
        return "ETRS89" in upper_name or "REGCAN95" in upper_name

    @property
    def native_utm_zone(self) -> int | None:
        """Return the UTM zone encoded by an observed IGN-CNIG filename."""

        if not self.is_native_projected_variant:
            return None
        tokens = self.filename.upper().replace("_", "-").split("-")
        for zone in (28, 29, 30, 31):
            if f"HU{zone}" in tokens or f"H{zone}" in tokens:
                return zone
        return None


@dataclass(frozen=True, slots=True)
class CnigCatalogPage:
    """One parsed page of IGN-CNIG catalog results."""

    total_items: int
    items: tuple[CnigCatalogItem, ...]


@dataclass(frozen=True, slots=True)
class CnigDiscoveryResult:
    """IGN-CNIG source files selected for one import plan."""

    items: tuple[CnigCatalogItem, ...]
    advertised_items: int
    ignored_items: int

    @property
    def estimated_download_mb(self) -> float | None:
        """Sum advertised sizes, or return None if any size is unknown."""

        if any(item.size_mb is None for item in self.items):
            return None
        return sum(item.size_mb or 0.0 for item in self.items)


# Compatibility aliases for the pre-0.6 provider API. New code should use the
# explicitly provider-scoped names above.
DatasetProduct = CnigProduct
ProductPage = CnigProductPage
CatalogItem = CnigCatalogItem
CatalogPage = CnigCatalogPage
DiscoveryResult = CnigDiscoveryResult
