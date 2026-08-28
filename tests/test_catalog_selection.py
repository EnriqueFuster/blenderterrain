from __future__ import annotations

from dataclasses import replace

import pytest

from blender_terrain.catalog import (
    AcquisitionRequest,
    CandidateSet,
    Catalog,
    DatasetKind,
    ImplementationStatus,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
    create_acquisition_plan,
    discover_candidates,
    load_bundled_catalog,
)
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import SelectionError

VALENCIA = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)


def test_plan_supports_independent_providers_for_each_layer() -> None:
    catalog = _with_global_products_available()
    request = AcquisitionRequest(
        VALENCIA,
        (
            LayerRequest(DatasetKind.DTM, 10.0),
            LayerRequest(DatasetKind.DSM, 20.0),
            LayerRequest(DatasetKind.IMAGERY, 10.0),
        ),
    )
    selections = SelectionBundle(
        (
            _selection(catalog, "GEDTM30_V11", DatasetKind.DTM),
            _selection(catalog, "MDS02", DatasetKind.DSM),
            _selection(catalog, "ESA_WORLDCOVER_S2_2021", DatasetKind.IMAGERY),
        )
    )

    plan = create_acquisition_plan(request, selections, _candidate_sets(catalog, request))

    assert {selection.provider_id for selection in plan.selections.selections} == {
        "openlandmap",
        "ign_cnig",
        "esa_worldcover",
    }


def test_unconfirmed_selection_cannot_create_a_plan() -> None:
    catalog = load_bundled_catalog()
    request = AcquisitionRequest(VALENCIA, (LayerRequest(DatasetKind.DTM),))
    selection = replace(_selection(catalog, "MDT02", DatasetKind.DTM), confirmed_by_user=False)

    with pytest.raises(SelectionError, match="Confirm every"):
        create_acquisition_plan(
            request,
            SelectionBundle((selection,)),
            _candidate_sets(catalog, request),
        )


def test_auto_is_explicit_confirmed_and_uses_the_recommendation() -> None:
    catalog = load_bundled_catalog()
    request = AcquisitionRequest(VALENCIA, (LayerRequest(DatasetKind.DTM),))
    candidates = _candidate_sets(catalog, request)
    automatic = replace(
        _selection(catalog, "MDT02", DatasetKind.DTM),
        mode=SelectionMode.AUTO,
    )

    plan = create_acquisition_plan(request, SelectionBundle((automatic,)), candidates)

    assert plan.selections.selections[0].mode is SelectionMode.AUTO
    with pytest.raises(SelectionError, match="displayed recommendation"):
        create_acquisition_plan(
            request,
            SelectionBundle(
                (
                    replace(
                        automatic,
                        product_id="MDT05",
                    ),
                )
            ),
            candidates,
        )


def test_rejected_product_cannot_be_locked_into_a_plan() -> None:
    catalog = load_bundled_catalog()
    request = AcquisitionRequest(VALENCIA, (LayerRequest(DatasetKind.DTM),))

    with pytest.raises(SelectionError, match="not a valid candidate"):
        create_acquisition_plan(
            request,
            SelectionBundle((_selection(catalog, "GEDTM30_V11", DatasetKind.DTM),)),
            _candidate_sets(catalog, request),
        )


def test_changed_roi_or_resolution_invalidates_the_plan() -> None:
    catalog = load_bundled_catalog()
    request = AcquisitionRequest(VALENCIA, (LayerRequest(DatasetKind.DTM, 10.0),))
    selections = SelectionBundle((_selection(catalog, "MDT02", DatasetKind.DTM),))
    plan = create_acquisition_plan(request, selections, _candidate_sets(catalog, request))

    moved = replace(request, roi=BBoxWGS84(-0.38, 39.46, -0.36, 39.48))
    changed_resolution = replace(
        request,
        layers=(LayerRequest(DatasetKind.DTM, 20.0),),
    )

    assert plan.is_current(request, selections)
    assert not plan.is_current(moved, selections)
    assert not plan.is_current(changed_resolution, selections)


def test_candidate_snapshot_from_an_old_roi_is_rejected() -> None:
    catalog = load_bundled_catalog()
    request = AcquisitionRequest(VALENCIA, (LayerRequest(DatasetKind.DTM),))
    stale = discover_candidates(
        catalog,
        BBoxWGS84(-3.71, 40.41, -3.69, 40.43),
        DatasetKind.DTM,
    )

    with pytest.raises(SelectionError, match="stale"):
        create_acquisition_plan(
            request,
            SelectionBundle((_selection(catalog, "MDT02", DatasetKind.DTM),)),
            (stale,),
        )


def _selection(
    catalog: Catalog,
    product_id: str,
    kind: DatasetKind,
) -> ProductSelection:
    product = catalog.product(product_id)
    return ProductSelection(
        provider_id=product.provider_id,
        product_id=product.id,
        kind=kind,
        mode=SelectionMode.MANUAL,
        confirmed_by_user=True,
    )


def _candidate_sets(
    catalog: Catalog,
    request: AcquisitionRequest,
) -> tuple[CandidateSet, ...]:
    return tuple(
        discover_candidates(
            catalog,
            request.roi,
            layer.kind,
            license_profile=request.license_profile,
        )
        for layer in request.layers
    )


def _with_global_products_available() -> Catalog:
    global_ids = {
        "GEDTM30_V11",
        "COPERNICUS_GLO30_2021",
        "ESA_WORLDCOVER_S2_2021",
    }
    catalog = load_bundled_catalog()
    return Catalog(
        tuple(
            replace(product, implementation_status=ImplementationStatus.EXPERIMENTAL)
            if product.id in global_ids
            else product
            for product in catalog.products
        )
    )
