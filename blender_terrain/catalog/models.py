"""Immutable domain models for geographic products and their constraints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.roi import BBoxWGS84


class DatasetKind(StrEnum):
    DTM = "dtm"
    DSM = "dsm"
    IMAGERY = "imagery"
    BATHYMETRY = "bathymetry"


class SemanticConfidence(StrEnum):
    MEASURED_DTM = "measured_dtm"
    DERIVED_DTM = "derived_dtm"
    MODELLED_DTM = "modelled_dtm"
    DSM = "dsm"
    BATHYMETRY = "bathymetry"
    ORTHOPHOTO = "orthophoto"
    SATELLITE_SURFACE_REFLECTANCE = "satellite_surface_reflectance"


class AcquisitionMode(StrEnum):
    CATALOG_TILES = "catalog_tiles"
    GLOBAL_COG = "global_cog"
    TILED_COG = "tiled_cog"
    WMS = "wms"
    STAC = "stac"


class ImplementationStatus(StrEnum):
    RESEARCHED = "researched"
    SPIKE = "spike"
    EXPERIMENTAL = "experimental"
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    RETIRED = "retired"


class CoverageMatch(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    POTENTIAL = "potential"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class LicensePolicy:
    identifier: str
    commercial_use: bool
    derivatives: bool
    redistribution: bool | None
    share_alike: bool
    attribution_required: bool
    attribution_text: str

    @property
    def commercial_safe(self) -> bool:
        return (
            self.commercial_use
            and self.derivatives
            and self.redistribution is not False
            and not self.share_alike
        )


@dataclass(frozen=True, slots=True)
class Coverage:
    bounds: tuple[BBoxWGS84, ...]
    requires_discovery: bool
    limitations: tuple[str, ...] = ()

    def match(self, roi: BBoxWGS84) -> CoverageMatch:
        """Classify bbox overlap without claiming detail absent from the catalog."""

        intersecting = tuple(bounds for bounds in self.bounds if _intersects(bounds, roi))
        if not intersecting:
            return CoverageMatch.NONE
        if self.requires_discovery:
            return CoverageMatch.POTENTIAL
        if any(_contains(bounds, roi) for bounds in intersecting):
            return CoverageMatch.FULL
        return CoverageMatch.PARTIAL


@dataclass(frozen=True, slots=True)
class ProductCapabilities:
    kind: DatasetKind
    native_resolution_m: float
    semantics: SemanticConfidence
    authoritative: bool
    acquisition_mode: AcquisitionMode
    supports_roi_window: bool
    supports_http_range: bool
    requires_auth: bool
    uncertainty_available: bool
    temporal: bool

    def __post_init__(self) -> None:
        if self.native_resolution_m <= 0:
            raise ValueError("Native resolution must be positive")
        if self.kind is DatasetKind.DTM and self.semantics not in {
            SemanticConfidence.MEASURED_DTM,
            SemanticConfidence.DERIVED_DTM,
            SemanticConfidence.MODELLED_DTM,
        }:
            raise ValueError("DTM products must declare DTM semantics")
        if self.kind is DatasetKind.DSM and self.semantics is not SemanticConfidence.DSM:
            raise ValueError("DSM products must declare DSM semantics")


@dataclass(frozen=True, slots=True)
class ProductRecord:
    id: str
    provider_id: str
    name: str
    jurisdiction: str
    implementation_status: ImplementationStatus
    version: str | None
    last_verified: str
    endpoint: str
    evidence_urls: tuple[str, ...]
    reliability_score: int
    network_cost_score: int
    capabilities: ProductCapabilities
    coverage: Coverage
    license: LicensePolicy

    def __post_init__(self) -> None:
        if not self.id or not self.provider_id or not self.name:
            raise ValueError("Product identity fields cannot be empty")
        for label, score in (
            ("Reliability", self.reliability_score),
            ("Network cost", self.network_cost_score),
        ):
            if not 0 <= score <= 100:
                raise ValueError(f"{label} score must be between 0 and 100")

    @property
    def selectable(self) -> bool:
        return self.implementation_status in {
            ImplementationStatus.EXPERIMENTAL,
            ImplementationStatus.SUPPORTED,
        }


@dataclass(frozen=True, slots=True)
class Catalog:
    products: tuple[ProductRecord, ...]

    def __post_init__(self) -> None:
        identifiers = [product.id for product in self.products]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Catalog product identifiers must be unique")

    def product(self, product_id: str) -> ProductRecord:
        for product in self.products:
            if product.id == product_id:
                return product
        raise KeyError(product_id)


def _intersects(left: BBoxWGS84, right: BBoxWGS84) -> bool:
    return not (
        left.east <= right.west
        or left.west >= right.east
        or left.north <= right.south
        or left.south >= right.north
    )


def _contains(container: BBoxWGS84, candidate: BBoxWGS84) -> bool:
    return (
        container.west <= candidate.west
        and container.south <= candidate.south
        and container.east >= candidate.east
        and container.north >= candidate.north
    )
