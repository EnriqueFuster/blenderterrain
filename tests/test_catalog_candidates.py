from __future__ import annotations

from dataclasses import replace

import pytest

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
LONDON = BBoxWGS84(-0.15, 51.49, -0.10, 51.53)
CARDIFF = BBoxWGS84(-3.19, 51.47, -3.17, 51.49)
EDINBURGH = BBoxWGS84(-3.20, 55.94, -3.18, 55.96)
BELFAST = BBoxWGS84(-5.94, 54.59, -5.91, 54.61)
NH24 = BBoxWGS84(-4.872, 57.480, -4.870, 57.482)


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


def test_osni_stays_unselectable_without_product_specific_crs_evidence() -> None:
    candidates = discover_candidates(load_bundled_catalog(), BELFAST, DatasetKind.DTM)
    osni = next(
        candidate
        for candidate in candidates.rejected
        if candidate.product.id == "GB_NIR_OSNI_10M_DTM"
    )
    assert osni.coverage.value == "potential"
    assert osni.rejection_reasons == (RejectionReason.PRODUCT_UNAVAILABLE,)
    assert any("Horizontal CRS" in limit for limit in osni.product.coverage.limitations)
    assert "GEDTM30_V11" in {candidate.product.id for candidate in candidates.valid}


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


def test_english_dtm_is_recommended_without_hiding_global_fallback() -> None:
    candidates = discover_candidates(load_bundled_catalog(), LONDON, DatasetKind.DTM)

    assert [candidate.product.id for candidate in candidates.valid] == [
        "GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM",
        "GEDTM30_V11",
    ]
    assert candidates.recommended is not None
    assert candidates.recommended.product.id == "GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM"


@pytest.mark.parametrize(
    ("roi", "dtm_product", "dsm_product"),
    [
        (
            LONDON,
            "GB_ENG_EA_LIDAR_COMPOSITE_1M_DTM",
            "GB_ENG_EA_LIDAR_COMPOSITE_1M_DSM_LAST_RETURN",
        ),
        (CARDIFF, "GB_WLS_DMW_LIDAR_1M_32F_DTM", "GB_WLS_DMW_LIDAR_1M_32F_DSM"),
        (EDINBURGH, None, None),
        (BELFAST, None, None),
    ],
)
def test_uk_roi_keeps_independent_official_and_global_choices(
    roi: BBoxWGS84, dtm_product: str | None, dsm_product: str | None
) -> None:
    catalog = load_bundled_catalog()
    expected = (
        (DatasetKind.DTM, dtm_product, "GEDTM30_V11"),
        (DatasetKind.DSM, dsm_product, "COPERNICUS_GLO30_2021"),
        (DatasetKind.IMAGERY, None, "ESA_WORLDCOVER_S2_2021"),
    )
    for kind, official_id, global_id in expected:
        candidates = discover_candidates(catalog, roi, kind)
        valid_ids = {candidate.product.id for candidate in candidates.valid}
        assert global_id in valid_ids
        if official_id is not None:
            assert official_id in valid_ids
        if roi == EDINBURGH and kind is not DatasetKind.IMAGERY:
            assert any(
                candidate.product.jurisdiction == "GB-SCT" for candidate in candidates.valid
            )
        assert not any(candidate.product.jurisdiction == "GB-NIR" for candidate in candidates.valid)
        assert all(candidate.product.selectable for candidate in candidates.valid)


@pytest.mark.parametrize("kind", [DatasetKind.DTM, DatasetKind.DSM])
def test_verified_scottish_asset_is_available_only_inside_nh24(kind: DatasetKind) -> None:
    candidates = discover_candidates(load_bundled_catalog(), NH24, kind)
    valid_ids = {candidate.product.id for candidate in candidates.valid}
    assert f"GB_SCT_SRSP_PHASE1_{kind.name}" in valid_ids
    assert not any(
        candidate.product.id.startswith("GB_SCT_SRSP_PHASE1_NH24_")
        for candidate in discover_candidates(load_bundled_catalog(), EDINBURGH, kind).valid
    )


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
