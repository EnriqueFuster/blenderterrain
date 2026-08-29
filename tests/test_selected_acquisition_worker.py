from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import numpy as np

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
from blender_terrain.core.elevation_processing import ProcessedElevationTile
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.io.imagery_window import write_imagery_window
from blender_terrain.jobs.acquisition_job import AcquisitionJob
from blender_terrain.jobs.storage import write_acquisition_job
from blender_terrain.jobs.worker import (
    acquire_confirmed_sources,
    prepare_confirmed_elevation,
    run_confirmed_acquisition_job,
)
from blender_terrain.models import ProjectedBounds


class Acquirer:
    def __init__(self) -> None:
        self.rois: list[BBoxWGS84] = []

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback=None,
        cancellation_requested=lambda: False,
    ) -> AcquiredRasterLayer:
        self.rois.append(roi)
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
    acquirer = Acquirer()

    def factory(provider_ids: tuple[str, ...]) -> dict[str, RasterAcquirer]:
        requested.append(provider_ids)
        return {"copernicus_dem": acquirer}

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
    assert acquirer.rois[0] == roi
    source_roi = acquirer.rois[1]
    assert source_roi.west <= roi.west
    assert source_roi.south <= roi.south
    assert source_roi.east >= roi.east
    assert source_roi.north >= roi.north
    assert source_roi != roi

    job_path = tmp_path / "jobs" / "task" / "job.json"
    write_acquisition_job(
        job_path,
        AcquisitionJob(str(uuid4()), str(uuid4()), plan),
    )

    def terrain_processor(paths, import_plan, progress_callback=None, **kwargs):
        tile = import_plan.tiles_for_grid(0)[0]
        if progress_callback is not None:
            progress_callback(1, 1)
        return (
            ProcessedElevationTile(
                0,
                tile,
                np.ones((tile.rows + 1, tile.columns + 1), dtype=np.float32),
                -9999.0,
                0,
                0,
                0.0,
            ),
        )

    state = run_confirmed_acquisition_job(
        job_path,
        acquirer_factory=factory,
        elevation_processor=terrain_processor,
    )
    payload = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))

    assert state.value == "COMPLETE"
    assert payload["request"]["product"] == "COPERNICUS_GLO30_2021"
    assert Path(payload["processed_elevation"][0]["path"]).is_file()


def test_worker_delivers_independently_selected_elevation_and_imagery(
    tmp_path: Path,
) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(2.34, 48.85, 2.341, 48.851)
    request = AcquisitionRequest(
        roi,
        (
            LayerRequest(DatasetKind.DSM, 30.0),
            LayerRequest(DatasetKind.IMAGERY, 10.0, "2021"),
        ),
    )
    selections = SelectionBundle(
        (
            ProductSelection(
                "copernicus_dem",
                "COPERNICUS_GLO30_2021",
                DatasetKind.DSM,
                SelectionMode.MANUAL,
                True,
            ),
            ProductSelection(
                "esa_worldcover",
                "ESA_WORLDCOVER_S2_2021",
                DatasetKind.IMAGERY,
                SelectionMode.MANUAL,
                True,
                "2021",
            ),
        )
    )
    plan = create_acquisition_plan(
        request,
        selections,
        (
            discover_candidates(catalog, roi, DatasetKind.DSM),
            discover_candidates(catalog, roi, DatasetKind.IMAGERY),
        ),
    )
    requested: list[tuple[str, ...]] = []

    class MultiLayerAcquirer(Acquirer):
        def acquire(
            self,
            selection,
            request,
            roi,
            cache_directory,
            progress_callback=None,
            cancellation_requested=lambda: False,
        ):
            if selection.kind is not DatasetKind.IMAGERY:
                return super().acquire(
                    selection,
                    request,
                    roi,
                    cache_directory,
                    progress_callback,
                    cancellation_requested,
                )
            source = cache_directory / "worldcover.npy"
            padding = 0.001
            bounds = ProjectedBounds(
                roi.west - padding,
                roi.south - padding,
                roi.east + padding,
                roi.north + padding,
                4326,
            )
            data = np.full((64, 64, 4), (0.05, 0.1, 0.2, 0.3), dtype=np.float32)
            write_imagery_window(
                source, data, bounds, 0.0, ("B02", "B03", "B04", "B08")
            )
            return AcquiredRasterLayer(
                selection.provider_id, selection.product_id, selection.kind, (source,)
            )

    acquirer = MultiLayerAcquirer()

    def factory(provider_ids: tuple[str, ...]) -> dict[str, RasterAcquirer]:
        requested.append(provider_ids)
        return {provider_id: acquirer for provider_id in provider_ids}

    job_path = tmp_path / "jobs" / "task" / "job.json"
    write_acquisition_job(job_path, AcquisitionJob(str(uuid4()), str(uuid4()), plan))

    def terrain_processor(paths, import_plan, progress_callback=None, **kwargs):
        tile = import_plan.tiles_for_grid(0)[0]
        return (
            ProcessedElevationTile(
                0,
                tile,
                np.ones((tile.rows + 1, tile.columns + 1), dtype=np.float32),
                -9999.0,
                0,
                0,
                0.0,
            ),
        )

    state = run_confirmed_acquisition_job(
        job_path,
        acquirer_factory=factory,
        elevation_processor=terrain_processor,
    )
    payload = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))

    assert state.value == "COMPLETE"
    assert requested == [("copernicus_dem",), ("esa_worldcover",)]
    assert [source["kind"] for source in payload["sources"]] == ["dsm", "imagery"]
    assert all(source["license"] for source in payload["sources"])
    assert payload["request"]["use_imagery"] is True
    assert payload["imagery"]
    assert all(Path(item["path"]).is_file() for item in payload["imagery"])
