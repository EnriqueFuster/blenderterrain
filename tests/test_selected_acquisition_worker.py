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
from blender_terrain.core.planning import create_import_plan
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.errors import NoCoverageError
from blender_terrain.io.elevation_window import write_elevation_window
from blender_terrain.io.imagery_window import write_imagery_window
from blender_terrain.io.png_validation import write_rgb_png
from blender_terrain.jobs.acquisition_job import AcquisitionJob
from blender_terrain.jobs.storage import write_acquisition_job
from blender_terrain.jobs.worker import (
    acquire_confirmed_sources,
    prepare_confirmed_bathymetry,
    prepare_confirmed_elevation,
    prepare_confirmed_imagery,
    run_confirmed_acquisition_job,
)
from blender_terrain.models import DatasetProduct, ProjectedBounds


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


def test_worker_delivers_independently_selected_elevation_bathymetry_and_imagery(
    tmp_path: Path,
) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(2.34, 48.85, 2.341, 48.851)
    request = AcquisitionRequest(
        roi,
        (
            LayerRequest(DatasetKind.DSM, 30.0),
            LayerRequest(DatasetKind.BATHYMETRY, 463.0),
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
                "gebco",
                "GEBCO_2026",
                DatasetKind.BATHYMETRY,
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
            discover_candidates(catalog, roi, DatasetKind.BATHYMETRY),
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
            if selection.kind is DatasetKind.BATHYMETRY:
                bounds = ProjectedBounds(
                    roi.west - 0.01,
                    roi.south - 0.01,
                    roi.east + 0.01,
                    roi.north + 0.01,
                    4326,
                )
                elevation_path = cache_directory / "bathymetry.npy"
                tid_path = cache_directory / "tid.npy"
                write_elevation_window(
                    elevation_path,
                    np.full((32, 32), -50.0, np.float32),
                    bounds,
                    -32768.0,
                )
                write_elevation_window(
                    tid_path,
                    np.full((32, 32), 11.0, np.float32),
                    bounds,
                    255.0,
                )
                return AcquiredRasterLayer(
                    selection.provider_id,
                    selection.product_id,
                    selection.kind,
                    (elevation_path,),
                    auxiliary_paths=(tid_path,),
                )
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
    events = [
        json.loads(line)
        for line in job_path.with_name("events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert state.value == "COMPLETE"
    assert requested == [("copernicus_dem",), ("gebco",), ("esa_worldcover",)]
    assert [source["kind"] for source in payload["sources"]] == [
        "dsm",
        "bathymetry",
        "imagery",
    ]
    assert all(source["license"] for source in payload["sources"])
    assert payload["request"]["use_imagery"] is True
    assert payload["imagery"]
    assert all(Path(item["path"]).is_file() for item in payload["imagery"])
    assert payload["request"]["use_bathymetry"] is True
    assert payload["bathymetry"]
    assert Path(payload["processed_elevation"][0]["marine_mask_path"]).is_file()
    assert payload["terrain_source"]
    progress = [event["progress"] for event in events]
    assert progress == sorted(progress)
    assert len(set(progress)) >= 8
    assert any(event["state"] == "DOWNLOADING_IMAGERY" for event in events)


def test_worker_builds_confirmed_bathymetry_when_glo30_has_no_ocean_data(
    tmp_path: Path,
) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(1.5, 54.5, 1.502, 54.502)
    request = AcquisitionRequest(
        roi,
        (
            LayerRequest(DatasetKind.DSM, 30.0),
            LayerRequest(DatasetKind.BATHYMETRY, 463.0),
            LayerRequest(DatasetKind.IMAGERY, 100.0, "2021"),
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
                "gebco",
                "GEBCO_2026",
                DatasetKind.BATHYMETRY,
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
            discover_candidates(catalog, roi, DatasetKind.BATHYMETRY),
            discover_candidates(catalog, roi, DatasetKind.IMAGERY),
        ),
    )

    class MarineAcquirer(Acquirer):
        def acquire(
            self,
            selection,
            request,
            roi,
            cache_directory,
            progress_callback=None,
            cancellation_requested=lambda: False,
        ):
            if selection.kind is DatasetKind.DSM:
                raise NoCoverageError("GLO-30 ocean tile is absent")
            if selection.kind is DatasetKind.IMAGERY:
                raise NoCoverageError("WorldCover ocean tile is absent")
            bounds = ProjectedBounds(
                roi.west - 0.01,
                roi.south - 0.01,
                roi.east + 0.01,
                roi.north + 0.01,
                4326,
            )
            elevation_path = cache_directory / "bathymetry.npy"
            tid_path = cache_directory / "tid.npy"
            values = np.linspace(-40.0, -15.0, 32 * 32, dtype=np.float32).reshape(32, 32)
            write_elevation_window(elevation_path, values, bounds, -32768.0)
            write_elevation_window(
                tid_path,
                np.full((32, 32), 11.0, np.float32),
                bounds,
                255.0,
            )
            return AcquiredRasterLayer(
                selection.provider_id,
                selection.product_id,
                selection.kind,
                (elevation_path,),
                auxiliary_paths=(tid_path,),
            )

    acquirer = MarineAcquirer()
    job_path = tmp_path / "jobs" / "task" / "job.json"
    write_acquisition_job(job_path, AcquisitionJob(str(uuid4()), str(uuid4()), plan))

    state = run_confirmed_acquisition_job(
        job_path,
        acquirer_factory=lambda provider_ids: {
            provider_id: acquirer for provider_id in provider_ids
        },
    )
    payload = json.loads(job_path.with_name("result.json").read_text(encoding="utf-8"))
    events = (job_path.with_name("events.jsonl")).read_text(encoding="utf-8")

    assert state.value == "COMPLETE_WITH_WARNINGS"
    assert payload["elevation_paths"] == []
    assert payload["imagery_paths"] == []
    assert payload["bathymetry"]
    assert payload["processed_elevation"]
    assert payload["sources"][0]["product_id"] == "GEBCO_2026"
    assert "Downloading GEBCO bathymetry" in events
    assert "continuing without texture" in events
    assert any("has no data for this ROI" in warning for warning in payload["warnings"])
    assert any("no imagery" in warning for warning in payload["warnings"])


def test_worker_prepares_confirmed_bathymetry_without_changing_provider(
    tmp_path: Path,
) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(-1.0, 50.0, -0.998, 50.002)
    request = AcquisitionRequest(roi, (LayerRequest(DatasetKind.BATHYMETRY, 463.0),))
    selection = ProductSelection(
        "gebco",
        "GEBCO_2026",
        DatasetKind.BATHYMETRY,
        SelectionMode.MANUAL,
        True,
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, DatasetKind.BATHYMETRY),),
    )
    import_plan = create_import_plan(
        roi,
        "COPERNICUS_GLO30_2021",
        30.0,
        False,
        None,
        native_resolution_override=30.0,
        use_global_utm=True,
    )
    requested: list[tuple[str, ...]] = []

    class BathymetryAcquirer(Acquirer):
        def acquire(
            self,
            selection,
            request,
            roi,
            cache_directory,
            progress_callback=None,
            cancellation_requested=lambda: False,
        ):
            bounds = ProjectedBounds(
                roi.west - 0.01,
                roi.south - 0.01,
                roi.east + 0.01,
                roi.north + 0.01,
                4326,
            )
            elevation_path = cache_directory / "bathymetry.npy"
            tid_path = cache_directory / "tid.npy"
            write_elevation_window(
                elevation_path, np.full((32, 32), -50.0, np.float32), bounds, -32768.0
            )
            write_elevation_window(
                tid_path, np.full((32, 32), 11.0, np.float32), bounds, 255.0
            )
            return AcquiredRasterLayer(
                selection.provider_id,
                selection.product_id,
                selection.kind,
                (elevation_path,),
                auxiliary_paths=(tid_path,),
            )

    def factory(provider_ids: tuple[str, ...]) -> dict[str, RasterAcquirer]:
        requested.append(provider_ids)
        return {"gebco": BathymetryAcquirer()}

    prepared = prepare_confirmed_bathymetry(
        plan,
        catalog,
        import_plan,
        tmp_path,
        acquirer_factory=factory,
    )

    assert prepared is not None
    assert requested == [("gebco",)]
    assert prepared.acquired.product_id == "GEBCO_2026"
    assert len(prepared.tiles) == import_plan.terrain_tile_count
    assert all(np.all(tile.elevation == -50.0) for tile in prepared.tiles)
    assert all(set(np.unique(tile.tid)) == {11} for tile in prepared.tiles)


def test_worker_prepares_confirmed_pnoa_tiles_without_global_fallback(
    tmp_path: Path,
) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(-0.381, 39.469, -0.379, 39.471)
    request = AcquisitionRequest(roi, (LayerRequest(DatasetKind.IMAGERY, 5.0),))
    selection = ProductSelection(
        "ign_pnoa",
        "PNOA_MA",
        DatasetKind.IMAGERY,
        SelectionMode.MANUAL,
        True,
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, DatasetKind.IMAGERY),),
    )
    import_plan = create_import_plan(
        roi,
        DatasetProduct.MDT02,
        10.0,
        True,
        5.0,
        native_resolution_override=2.0,
    )

    class FakePnoaClient:
        def download_png(
            self,
            bounds,
            width,
            height,
            cache_directory,
            filename,
            progress_callback=None,
        ):
            path = cache_directory / filename
            write_rgb_png(path, np.zeros((height, width, 3), dtype=np.uint8))
            if progress_callback is not None:
                progress_callback(path.stat().st_size, path.stat().st_size)
            return path

    prepared = prepare_confirmed_imagery(
        plan,
        catalog,
        import_plan,
        tmp_path,
        tmp_path / "imagery",
        pnoa_factory=FakePnoaClient,
    )

    assert prepared is not None
    assert prepared.acquired.provider_id == "ign_pnoa"
    assert prepared.acquired.product_id == "PNOA_MA"
    assert len(prepared.tiles) == import_plan.imagery.tile_count
    assert all(tile.path.is_file() for tile in prepared.tiles)


def test_worker_uses_projected_bd_ortho_without_reprojection(tmp_path: Path) -> None:
    catalog = load_bundled_catalog()
    roi = BBoxWGS84(2.34, 48.85, 2.36, 48.87)
    request = AcquisitionRequest(roi, (LayerRequest(DatasetKind.IMAGERY, 5.0),))
    selection = ProductSelection(
        "ign_france",
        "FR_BD_ORTHO",
        DatasetKind.IMAGERY,
        SelectionMode.MANUAL,
        True,
    )
    plan = create_acquisition_plan(
        request,
        SelectionBundle((selection,)),
        (discover_candidates(catalog, roi, DatasetKind.IMAGERY),),
    )
    import_plan = create_import_plan(
        roi,
        "FR_RGE_ALTI_1M",
        10.0,
        True,
        5.0,
        native_resolution_override=1.0,
        working_crs_epsg=2154,
    )

    class FakeGeopfAcquirer:
        def acquire(
            self,
            selection,
            request,
            roi,
            cache_directory,
            progress_callback=None,
            cancellation_requested=lambda: False,
        ):
            path = cache_directory / "ortho.png"
            write_rgb_png(path, np.zeros((3, 4, 3), dtype=np.uint8))
            sidecar = path.with_suffix(".png.json")
            sidecar.write_text(
                json.dumps(
                    {
                        "bbox": [650_000.0, 6_860_000.0, 650_020.0, 6_860_015.0],
                        "width": 4,
                        "height": 3,
                        "crs_epsg": 2154,
                    }
                ),
                encoding="utf-8",
            )
            return AcquiredRasterLayer(
                "ign_france",
                "FR_BD_ORTHO",
                DatasetKind.IMAGERY,
                (path,),
                auxiliary_paths=(sidecar,),
            )

    prepared = prepare_confirmed_imagery(
        plan,
        catalog,
        import_plan,
        tmp_path,
        tmp_path / "processed",
        acquirer_factory=lambda provider_ids: {"ign_france": FakeGeopfAcquirer()},
    )

    assert prepared is not None
    assert prepared.tiles[0].path == tmp_path / "ortho.png"
    assert prepared.tiles[0].bounds.epsg == 2154
    assert prepared.tiles[0].gsd_metres == 5.0
