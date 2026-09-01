"""Portable product catalog contracts."""

from .candidates import (
    CandidateSet,
    LicenseProfile,
    ProductCandidate,
    RejectionReason,
    discover_candidates,
)
from .loader import load_bundled_catalog
from .models import (
    AcquisitionMode,
    Catalog,
    Coverage,
    CoverageMatch,
    DatasetKind,
    ImplementationStatus,
    LicensePolicy,
    ProductCapabilities,
    ProductRecord,
    SemanticConfidence,
    WMSContract,
)
from .selection import (
    AcquisitionPlan,
    AcquisitionRequest,
    FailurePolicy,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
    create_acquisition_plan,
)

__all__ = [
    "AcquisitionMode",
    "AcquisitionPlan",
    "AcquisitionRequest",
    "CandidateSet",
    "Catalog",
    "Coverage",
    "CoverageMatch",
    "DatasetKind",
    "FailurePolicy",
    "ImplementationStatus",
    "LayerRequest",
    "LicensePolicy",
    "LicenseProfile",
    "ProductCandidate",
    "ProductCapabilities",
    "ProductRecord",
    "ProductSelection",
    "RejectionReason",
    "SelectionBundle",
    "SelectionMode",
    "SemanticConfidence",
    "WMSContract",
    "create_acquisition_plan",
    "discover_candidates",
    "load_bundled_catalog",
]
