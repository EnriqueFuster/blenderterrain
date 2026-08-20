"""Small immutable models required by the Phase 0 catalog experiment."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


class DatasetProduct(StrEnum):
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

    @property
    def native_utm_zone(self) -> int | None:
        """Return the UTM zone encoded by an observed native CNIG filename."""

        if not self.is_native_projected_variant:
            return None
        tokens = self.filename.upper().replace("_", "-").split("-")
        for zone in (28, 29, 30, 31):
            if f"HU{zone}" in tokens or f"H{zone}" in tokens:
                return zone
        return None


@dataclass(frozen=True, slots=True)
class CatalogPage:
    """One parsed page of catalog results."""

    total_items: int
    items: tuple[CatalogItem, ...]


@dataclass(frozen=True, slots=True)
class ProjectedBounds:
    """Rectangular bounds expressed in one projected EPSG coordinate system."""

    west: float
    south: float
    east: float
    north: float
    epsg: int

    def __post_init__(self) -> None:
        coordinates = (self.west, self.south, self.east, self.north)
        if not all(math.isfinite(value) for value in coordinates):
            raise ValueError("Projected bounds must contain only finite coordinates")
        if self.east <= self.west or self.north <= self.south:
            raise ValueError("Projected bounds must have positive width and height")
        if self.epsg <= 0:
            raise ValueError("EPSG code must be positive")
