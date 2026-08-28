"""Deterministic product candidate discovery without network access."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.roi import BBoxWGS84
from .models import (
    Catalog,
    CoverageMatch,
    DatasetKind,
    ProductRecord,
    SemanticConfidence,
)


class LicenseProfile(StrEnum):
    COMMERCIAL_SAFE = "commercial_safe"


class RejectionReason(StrEnum):
    NO_COVERAGE = "no_coverage"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    LICENSE_INCOMPATIBLE = "license_incompatible"


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    product: ProductRecord
    coverage: CoverageMatch
    rejection_reasons: tuple[RejectionReason, ...]
    explanation: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True, slots=True)
class CandidateSet:
    roi: BBoxWGS84
    kind: DatasetKind
    license_profile: LicenseProfile
    valid: tuple[ProductCandidate, ...]
    rejected: tuple[ProductCandidate, ...]
    recommended: ProductCandidate | None

    def __post_init__(self) -> None:
        if any(not candidate.valid for candidate in self.valid):
            raise ValueError("Valid candidates cannot contain rejection reasons")
        if any(candidate.valid for candidate in self.rejected):
            raise ValueError("Rejected candidates must contain a rejection reason")
        if self.recommended is not None and self.recommended not in self.valid:
            raise ValueError("Recommended candidate must belong to the valid candidates")


def discover_candidates(
    catalog: Catalog,
    roi: BBoxWGS84,
    kind: DatasetKind,
    *,
    license_profile: LicenseProfile = LicenseProfile.COMMERCIAL_SAFE,
) -> CandidateSet:
    """Evaluate and rank every catalog product for one independent layer kind."""

    evaluated = tuple(
        _evaluate(product, roi, license_profile)
        for product in catalog.products
        if product.capabilities.kind is kind
    )
    valid = tuple(sorted((item for item in evaluated if item.valid), key=_rank_key))
    rejected = tuple(sorted((item for item in evaluated if not item.valid), key=_rank_key))
    return CandidateSet(
        roi=roi,
        kind=kind,
        license_profile=license_profile,
        valid=valid,
        rejected=rejected,
        recommended=valid[0] if valid else None,
    )


def _evaluate(
    product: ProductRecord,
    roi: BBoxWGS84,
    license_profile: LicenseProfile,
) -> ProductCandidate:
    coverage = product.coverage.match(roi)
    reasons: list[RejectionReason] = []
    if coverage is CoverageMatch.NONE:
        reasons.append(RejectionReason.NO_COVERAGE)
    if not product.selectable:
        reasons.append(RejectionReason.PRODUCT_UNAVAILABLE)
    if license_profile is LicenseProfile.COMMERCIAL_SAFE and not product.license.commercial_safe:
        reasons.append(RejectionReason.LICENSE_INCOMPATIBLE)
    explanation = (
        _coverage_explanation(coverage),
        f"{product.capabilities.native_resolution_m:g} m native resolution",
        product.capabilities.semantics.value,
        (
            "authoritative source"
            if product.capabilities.authoritative
            else "non-authoritative source"
        ),
        f"reliability {product.reliability_score}/100",
        f"network cost {product.network_cost_score}/100",
        f"license {product.license.identifier}",
    )
    return ProductCandidate(product, coverage, tuple(reasons), explanation)


def _rank_key(candidate: ProductCandidate) -> tuple[int, int, int, int, float, int, str]:
    product = candidate.product
    return (
        -_coverage_rank(candidate.coverage),
        -int(product.capabilities.authoritative),
        -_semantic_rank(product.capabilities.semantics),
        -product.reliability_score,
        product.capabilities.native_resolution_m,
        product.network_cost_score,
        product.id,
    )


def _coverage_rank(coverage: CoverageMatch) -> int:
    return {
        CoverageMatch.NONE: 0,
        CoverageMatch.PARTIAL: 1,
        CoverageMatch.POTENTIAL: 2,
        CoverageMatch.FULL: 3,
    }[coverage]


def _semantic_rank(semantics: SemanticConfidence) -> int:
    return {
        SemanticConfidence.MEASURED_DTM: 4,
        SemanticConfidence.DERIVED_DTM: 3,
        SemanticConfidence.ORTHOPHOTO: 3,
        SemanticConfidence.DSM: 2,
        SemanticConfidence.MODELLED_DTM: 1,
        SemanticConfidence.SATELLITE_SURFACE_REFLECTANCE: 1,
        SemanticConfidence.BATHYMETRY: 1,
    }[semantics]


def _coverage_explanation(coverage: CoverageMatch) -> str:
    return {
        CoverageMatch.FULL: "full catalog coverage",
        CoverageMatch.PARTIAL: "partial catalog coverage",
        CoverageMatch.POTENTIAL: "potential coverage; provider discovery required",
        CoverageMatch.NONE: "outside catalog coverage",
    }[coverage]
