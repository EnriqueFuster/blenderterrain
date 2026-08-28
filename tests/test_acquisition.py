from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blender_terrain.catalog import (
    AcquisitionRequest,
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
from blender_terrain.core.acquisition import AcquiredRasterLayer, acquire_plan_layers
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import ProviderUnavailableError

ROI = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)


class RecordingAcquirer:
    def __init__(self, path: Path, *, fail: bool = False) -> None:
        self.path = path
        self.fail = fail
        self.calls: list[ProductSelection] = []

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback=None,
        cancellation_requested=lambda: False,
    ) -> AcquiredRasterLayer:
        self.calls.append(selection)
        if self.fail:
            raise ProviderUnavailableError("selected provider is offline")
        return AcquiredRasterLayer(
            selection.provider_id,
            selection.product_id,
            selection.kind,
            (self.path,),
        )


def test_executes_independent_confirmed_providers(tmp_path: Path) -> None:
    plan = _plan(("MDT02", DatasetKind.DTM), ("COPERNICUS_GLO30_2021", DatasetKind.DSM))
    ign = RecordingAcquirer(tmp_path / "ign.tif")
    copernicus = RecordingAcquirer(tmp_path / "global.tif")

    result = acquire_plan_layers(
        plan,
        {"ign_cnig": ign, "copernicus_dem": copernicus},
        tmp_path,
    )

    assert [layer.provider_id for layer in result] == ["ign_cnig", "copernicus_dem"]
    assert [call.product_id for call in ign.calls] == ["MDT02"]
    assert [call.product_id for call in copernicus.calls] == ["COPERNICUS_GLO30_2021"]


def test_selected_provider_failure_does_not_invoke_an_alternative(tmp_path: Path) -> None:
    plan = _plan(("COPERNICUS_GLO30_2021", DatasetKind.DSM))
    selected = RecordingAcquirer(tmp_path / "selected.tif", fail=True)
    fallback = RecordingAcquirer(tmp_path / "fallback.tif")

    with pytest.raises(ProviderUnavailableError, match="offline"):
        acquire_plan_layers(
            plan,
            {"copernicus_dem": selected, "unselected": fallback},
            tmp_path,
        )

    assert len(selected.calls) == 1
    assert fallback.calls == []


def _plan(*products: tuple[str, DatasetKind]):
    catalog = load_bundled_catalog()
    enabled_ids = {product_id for product_id, _kind in products}
    catalog = Catalog(
        tuple(
            replace(product, implementation_status=ImplementationStatus.EXPERIMENTAL)
            if product.id in enabled_ids
            else product
            for product in catalog.products
        )
    )
    request = AcquisitionRequest(ROI, tuple(LayerRequest(kind) for _id, kind in products))
    selections = SelectionBundle(
        tuple(
            ProductSelection(
                catalog.product(product_id).provider_id,
                product_id,
                kind,
                SelectionMode.MANUAL,
                True,
            )
            for product_id, kind in products
        )
    )
    candidates = tuple(discover_candidates(catalog, ROI, kind) for _id, kind in products)
    return create_acquisition_plan(request, selections, candidates)
