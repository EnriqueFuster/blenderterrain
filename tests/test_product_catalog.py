from __future__ import annotations

from importlib.resources import files as resource_files

import pytest

from blender_terrain.catalog import (
    Coverage,
    CoverageMatch,
    DatasetKind,
    ImplementationStatus,
    SemanticConfidence,
    load_bundled_catalog,
    loader,
)
from blender_terrain.catalog.loader import load_catalog_documents
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.models import DatasetProduct


def test_catalog_contains_existing_spanish_products() -> None:
    catalog = load_bundled_catalog()
    spanish_ids = {
        product.id for product in catalog.products if product.jurisdiction == "ES"
    }

    assert spanish_ids == {product.value for product in DatasetProduct}
    assert all(catalog.product(product_id).selectable for product_id in spanish_ids)


def test_bundled_catalog_uses_its_runtime_package_namespace(monkeypatch) -> None:
    requested: list[str] = []

    def capture(anchor: str):
        requested.append(anchor)
        return resource_files(anchor)

    monkeypatch.setattr(loader, "files", capture)

    load_bundled_catalog()

    assert requested == [f"{loader.__package__}.data"]


def test_global_product_semantics_cannot_confuse_dtm_and_dsm() -> None:
    catalog = load_bundled_catalog()
    gedtm = catalog.product("GEDTM30_V11")
    glo30 = catalog.product("COPERNICUS_GLO30_2021")
    worldcover = catalog.product("ESA_WORLDCOVER_S2_2021")

    assert gedtm.capabilities.kind is DatasetKind.DTM
    assert gedtm.capabilities.semantics is SemanticConfidence.MODELLED_DTM
    assert gedtm.capabilities.uncertainty_available
    assert glo30.capabilities.kind is DatasetKind.DSM
    assert glo30.capabilities.semantics is SemanticConfidence.DSM
    assert worldcover.capabilities.kind is DatasetKind.IMAGERY
    assert worldcover.version == "2021"


def test_researched_products_are_not_selectable() -> None:
    catalog = load_bundled_catalog()
    researched = tuple(
        product
        for product in catalog.products
        if product.implementation_status is ImplementationStatus.RESEARCHED
    )

    assert {product.jurisdiction for product in researched} == {"FR", "CH"}
    assert researched
    assert not any(product.selectable for product in researched)


def test_selectable_products_have_commercial_safe_license_metadata() -> None:
    selectable = tuple(
        product for product in load_bundled_catalog().products if product.selectable
    )

    assert selectable
    assert all(product.license.commercial_safe for product in selectable)


def test_coarse_coverage_does_not_claim_full_product_coverage() -> None:
    catalog = load_bundled_catalog()
    valencia = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
    paris = BBoxWGS84(2.34, 48.85, 2.36, 48.87)

    assert catalog.product("MDT02").coverage.match(valencia) is CoverageMatch.POTENTIAL
    assert catalog.product("GEDTM30_V11").coverage.match(paris) is CoverageMatch.POTENTIAL
    assert catalog.product("MDT02").coverage.match(paris) is CoverageMatch.NONE


def test_exact_coverage_distinguishes_full_partial_and_none() -> None:
    coverage = Coverage((BBoxWGS84(0.0, 0.0, 10.0, 10.0),), False)

    assert coverage.match(BBoxWGS84(1.0, 1.0, 2.0, 2.0)) is CoverageMatch.FULL
    assert coverage.match(BBoxWGS84(9.0, 9.0, 11.0, 11.0)) is CoverageMatch.PARTIAL
    assert coverage.match(BBoxWGS84(10.0, 1.0, 11.0, 2.0)) is CoverageMatch.NONE


def test_catalog_rejects_unknown_fields() -> None:
    malformed = b"products = []\nunexpected = true\n"

    with pytest.raises(ValueError, match="unknown unexpected"):
        load_catalog_documents((("malformed.toml", malformed),))


def test_catalog_rejects_duplicate_product_ids() -> None:
    catalog = load_bundled_catalog()
    first = catalog.products[0]

    with pytest.raises(ValueError, match="identifiers must be unique"):
        type(catalog)((first, first))
