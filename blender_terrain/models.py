"""Small immutable models required by the Phase 0 catalog experiment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DatasetProduct(str, Enum):
    """Geographic products supported by the initial CNIG integration."""

    MDT02 = "MDT02"
    MDS02 = "MDS02"
    PNOA_MA = "PNOA_MA"


@dataclass(frozen=True, slots=True)
class ProductPage:
    """Dynamic fields extracted from a CNIG product page."""

    catalog_group: str
    catalog_series: str
    advertised_total: int
    attribution_ids: str
    formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogItem:
    """A downloadable resource advertised by the CNIG catalog."""

    product: DatasetProduct
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


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """One parsed page of catalog results."""

    total_items: int
    items: tuple[CatalogItem, ...]
