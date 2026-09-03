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
        "GEDTM30_V11",
    }
    gedtm = next(
        candidate
        for candidate in candidates.valid
        if candidate.product.id == "GEDTM30_V11"
    )
    assert gedtm.coverage.value == "potential"
    assert gedtm.rejection_reasons == ()


def test_implemented_global_dtm_coexists_with_ign_but_is_not_forced() -> None:
    catalog = _with_status("GEDTM30_V11", ImplementationStatus.EXPERIMENTAL)

    candidates = discover_candidates(catalog, VALENCIA, DatasetKind.DTM)

    assert "GEDTM30_V11" in {candidate.product.id for candidate in candidates.valid}
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "MDT02"


def test_french_dtm_is_recommended_without_hiding_global_fallback() -> None:
    candidates = discover_candidates(load_bundled_catalog(), PARIS, DatasetKind.DTM)

    assert [candidate.product.id for candidate in candidates.valid] == [
        "FR_RGE_ALTI_1M",
        "GEDTM30_V11",
    ]
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "FR_RGE_ALTI_1M"
    assert all(candidate.coverage.value == "potential" for candidate in candidates.valid)


def test_french_roi_exposes_national_and_global_dsm() -> None:
    candidates = discover_candidates(load_bundled_catalog(), PARIS, DatasetKind.DSM)

    assert [candidate.product.id for candidate in candidates.valid] == [
        "FR_MNS_CORREL_50CM",
        "COPERNICUS_GLO30_2021"
    ]
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "FR_MNS_CORREL_50CM"


def test_french_roi_exposes_bd_ortho_and_worldcover() -> None:
    candidates = discover_candidates(load_bundled_catalog(), PARIS, DatasetKind.IMAGERY)

    assert [candidate.product.id for candidate in candidates.valid] == [
        "FR_BD_ORTHO",
        "ESA_WORLDCOVER_S2_2021",
    ]
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "FR_BD_ORTHO"


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


def test_global_bathymetry_is_available_independently() -> None:
    candidates = discover_candidates(load_bundled_catalog(), PARIS, DatasetKind.BATHYMETRY)

    assert [candidate.product.id for candidate in candidates.valid] == ["GEBCO_2026"]
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "GEBCO_2026"


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
