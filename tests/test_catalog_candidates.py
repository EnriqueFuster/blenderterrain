from __future__ import annotations

from dataclasses import replace

from blender_terrain.catalog import (
    Catalog,
    DatasetKind,
    ImplementationStatus,
    RejectionReason,
    discover_candidates,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84

VALENCIA = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
PARIS = BBoxWGS84(2.34, 48.85, 2.36, 48.87)


def test_spanish_dtm_candidates_are_ranked_without_hiding_global_product() -> None:
    candidates = discover_candidates(load_bundled_catalog(), VALENCIA, DatasetKind.DTM)

    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "MDT02"
    assert {candidate.product.id for candidate in candidates.valid} == {
        "MDT50CM",
        "MDT02",
        "MDT05",
        "MDT25",
        "MDT200",
    }
    gedtm = next(
        candidate
        for candidate in candidates.rejected
        if candidate.product.id == "GEDTM30_V11"
    )
    assert gedtm.coverage.value == "potential"
    assert gedtm.rejection_reasons == (RejectionReason.PRODUCT_UNAVAILABLE,)


def test_implemented_global_dtm_coexists_with_ign_but_is_not_forced() -> None:
    catalog = _with_status("GEDTM30_V11", ImplementationStatus.EXPERIMENTAL)

    candidates = discover_candidates(catalog, VALENCIA, DatasetKind.DTM)

    assert "GEDTM30_V11" in {candidate.product.id for candidate in candidates.valid}
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "MDT02"


def test_french_roi_uses_global_only_after_global_provider_is_implemented() -> None:
    catalog = _with_status("GEDTM30_V11", ImplementationStatus.EXPERIMENTAL)

    candidates = discover_candidates(catalog, PARIS, DatasetKind.DTM)

    assert [candidate.product.id for candidate in candidates.valid] == ["GEDTM30_V11"]
    french = next(
        candidate
        for candidate in candidates.rejected
        if candidate.product.id == "FR_RGE_ALTI_1M"
    )
    assert french.rejection_reasons == (RejectionReason.PRODUCT_UNAVAILABLE,)


def test_layer_kinds_are_resolved_independently() -> None:
    catalog = load_bundled_catalog()

    dtm = discover_candidates(catalog, VALENCIA, DatasetKind.DTM)
    dsm = discover_candidates(catalog, VALENCIA, DatasetKind.DSM)
    imagery = discover_candidates(catalog, VALENCIA, DatasetKind.IMAGERY)

    assert dtm.recommended is not None and dtm.recommended.product.id == "MDT02"
    assert dsm.recommended is not None and dsm.recommended.product.id == "MDS02"
    assert imagery.recommended is not None and imagery.recommended.product.id == "PNOA_MA"


def test_license_incompatibility_rejects_an_otherwise_valid_product() -> None:
    catalog = load_bundled_catalog()
    mdt02 = catalog.product("MDT02")
    incompatible_license = replace(mdt02.license, commercial_use=False)
    changed = replace(mdt02, license=incompatible_license)
    altered = Catalog(
        tuple(changed if product.id == changed.id else product for product in catalog.products)
    )

    candidates = discover_candidates(altered, VALENCIA, DatasetKind.DTM)
    rejected = next(item for item in candidates.rejected if item.product.id == "MDT02")

    assert RejectionReason.LICENSE_INCOMPATIBLE in rejected.rejection_reasons
    assert candidates.recommended is not None
    assert candidates.recommended.product.id != "MDT02"


def test_empty_layer_has_no_recommendation() -> None:
    candidates = discover_candidates(load_bundled_catalog(), PARIS, DatasetKind.BATHYMETRY)

    assert candidates.valid == ()
    assert candidates.rejected == ()
    assert candidates.recommended is None


def _with_status(product_id: str, status: ImplementationStatus) -> Catalog:
    catalog = load_bundled_catalog()
    return Catalog(
        tuple(
            replace(product, implementation_status=status)
            if product.id == product_id
            else product
            for product in catalog.products
        )
    )
