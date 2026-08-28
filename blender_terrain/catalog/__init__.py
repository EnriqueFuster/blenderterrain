"""Portable product catalog contracts."""

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
)

__all__ = [
    "AcquisitionMode",
    "Catalog",
    "Coverage",
    "CoverageMatch",
    "DatasetKind",
    "ImplementationStatus",
    "LicensePolicy",
    "ProductCapabilities",
    "ProductRecord",
    "SemanticConfidence",
    "load_bundled_catalog",
]
