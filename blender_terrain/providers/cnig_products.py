"""Legacy CNIG product metadata retained outside the generic planner."""

from __future__ import annotations

from ..models import DatasetProduct

NATIVE_RESOLUTION_METRES = {
    DatasetProduct.MDT50CM: 0.5,
    DatasetProduct.MDT02: 2.0,
    DatasetProduct.MDT05: 5.0,
    DatasetProduct.MDT25: 25.0,
    DatasetProduct.MDT200: 200.0,
    DatasetProduct.MDS50CM: 0.5,
    DatasetProduct.MDS02: 2.0,
    DatasetProduct.MDS05: 5.0,
}


def legacy_native_resolution(product: str) -> float | None:
    """Return native resolution for a product from the pre-catalog CNIG API."""

    try:
        cnig_product = DatasetProduct(product)
    except ValueError:
        return None
    return NATIVE_RESOLUTION_METRES.get(cnig_product)
