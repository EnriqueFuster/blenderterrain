from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
from blender_terrain.core.acquisition import AcquiredRasterLayer, RasterAcquirer
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.jobs.worker import (
    acquire_confirmed_sources,
    prepare_confirmed_elevation,
)


class Acquirer:
    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback=None,
        cancellation_requested=lambda: False,
    ) -> AcquiredRasterLayer:
        return AcquiredRasterLayer(
            selection.provider_id,
            selection.product_id,
            selection.kind,
            (cache_directory / f"{selection.product_id}.tif",),
        )


def test_worker_constructs_only_adapters_locked_in_the_plan(tmp_path: Path) -> None:
    catalog = load_bundled_catalog()
    product = catalog.product("COPERNICUS_GLO30_2021")
    catalog = Catalog(
        tuple(
            replace(item, implementation_status=ImplementationStatus.EXPERIMENTAL)
            if item.id == product.id
            else item
            for item in catalog.products
        )
    )
    roi = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
    request = AcquisitionRequest(roi, (LayerRequest(DatasetKind.DSM),))
    selection = ProductSelection(
        product.provider_id,
        product.id,
        DatasetKind.DSM,
        SelectionMode.MANUAL,
        True,
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, DatasetKind.DSM),),
    )
    requested: list[tuple[str, ...]] = []

    def factory(provider_ids: tuple[str, ...]) -> dict[str, RasterAcquirer]:
        requested.append(provider_ids)
        return {"copernicus_dem": Acquirer()}

    result = acquire_confirmed_sources(plan, tmp_path, acquirer_factory=factory)

    processed_inputs = []

    def process(paths, import_plan, progress_callback=None, **kwargs):
        processed_inputs.append((paths, import_plan))
        if progress_callback is not None:
            progress_callback(0, 0)
        return ()

    prepared = prepare_confirmed_elevation(
        plan,
        catalog,
        tmp_path,
        acquirer_factory=factory,
        elevation_processor=process,
    )

    assert requested == [("copernicus_dem",), ("copernicus_dem",)]
    assert result[0].product_id == "COPERNICUS_GLO30_2021"
    assert prepared.import_plan.product == "COPERNICUS_GLO30_2021"
    assert prepared.import_plan.elevation_resolution_metres == 30.0
    assert processed_inputs[0][0] == prepared.acquired.paths
