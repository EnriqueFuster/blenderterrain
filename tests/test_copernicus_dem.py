from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

from blender_terrain.catalog import (
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionMode,
)
from blender_terrain.core.delivery import TransferProgress
from blender_terrain.core.elevation_processing import process_elevation_tiles
from blender_terrain.core.planning import create_import_plan
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.io.bigtiff_tiles import ClassicTiffFloatTileReader
from blender_terrain.io.http_download import DownloadedAsset
from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.copernicus_dem import (
    CopernicusGlo30Acquirer,
    glo30_tiles_for_roi,
)


def test_discovers_valencia_glo30_tile() -> None:
    tiles = glo30_tiles_for_roi(BBoxWGS84(-0.39, 39.46, -0.37, 39.48))

    assert len(tiles) == 1
    assert "N39_00_W001_00" in tiles[0].url
    assert tiles[0].bounds == BBoxWGS84(-1.0, 39.0, 0.0, 40.0)


def test_discovers_multiple_tiles_in_stable_geographic_order() -> None:
    tiles = glo30_tiles_for_roi(BBoxWGS84(-0.1, 39.9, 1.1, 41.1))

    assert [(tile.longitude, tile.latitude) for tile in tiles] == [
        (-1, 41),
        (0, 41),
        (1, 41),
        (-1, 40),
        (0, 40),
        (1, 40),
        (-1, 39),
        (0, 39),
        (1, 39),
    ]


def test_exact_east_and_north_boundaries_do_not_add_tiles() -> None:
    tiles = glo30_tiles_for_roi(BBoxWGS84(0.0, 39.0, 1.0, 40.0))

    assert [(tile.longitude, tile.latitude) for tile in tiles] == [(0, 39)]


def test_acquires_confirmed_glo30_selection_into_provider_cache(
    tmp_path: Path, monkeypatch,
) -> None:
    calls: list[tuple[str, Path, str]] = []

    def download(url: str, directory: Path, filename: str, **kwargs) -> DownloadedAsset:
        calls.append((url, directory, filename))
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        path.write_bytes(b"II*\x00\x08\x00\x00\x00\x00")
        kwargs["progress_callback"](9, 9)
        return DownloadedAsset(path, 9, False)

    monkeypatch.setattr(
        "blender_terrain.providers.copernicus_dem.download_public_tiff", download
    )
    selection = ProductSelection(
        "copernicus_dem",
        "COPERNICUS_GLO30_2021",
        DatasetKind.DSM,
        SelectionMode.MANUAL,
        True,
    )
    progress: list[TransferProgress] = []

    result = CopernicusGlo30Acquirer().acquire(
        selection,
        LayerRequest(DatasetKind.DSM),
        BBoxWGS84(-0.39, 39.46, -0.37, 39.48),
        tmp_path,
        progress.append,
    )

    assert result.paths == (calls[0][1] / calls[0][2],)
    assert calls[0][1].parts[-2:] == ("copernicus_dem", "COPERNICUS_GLO30_2021")
    assert progress[0].kind == "dsm"


def test_reads_classic_geographic_float_tiff_window(tmp_path: Path) -> None:
    path = tmp_path / "glo30.tif"
    values = np.arange(16, dtype="<f4").reshape(4, 4)
    _write_classic_float_tiff(path, values, predictor=3, declare_nodata=False)

    reader = ClassicTiffFloatTileReader(path)
    data, bounds = reader.read_bounds(ProjectedBounds(-0.75, 39.25, -0.25, 39.75, 4326))

    np.testing.assert_array_equal(data, values[1:3, 1:3])
    assert bounds == ProjectedBounds(-0.75, 39.25, -0.25, 39.75, 4326)
    assert reader.georeference.epsg == 4326


def test_processes_geographic_glo30_source_on_projected_terrain_grid(
    tmp_path: Path,
) -> None:
    path = tmp_path / "glo30.tif"
    values = np.arange(16, dtype="<f4").reshape(4, 4)
    _write_classic_float_tiff(path, values, predictor=3, declare_nodata=False)
    roi = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
    plan = create_import_plan(
        roi,
        "COPERNICUS_GLO30_2021",
        100.0,
        False,
        None,
        native_resolution_override=30.0,
    )

    processed = process_elevation_tiles((path,), plan)

    assert len(processed) == 1
    assert processed[0].tile.bounds.epsg == 25830
    assert np.all(processed[0].data != processed[0].nodata)
    assert float(processed[0].data.min()) >= 0.0
    assert float(processed[0].data.max()) <= 15.0


def _write_classic_float_tiff(
    path: Path,
    values: np.ndarray,
    *,
    predictor: int,
    declare_nodata: bool = True,
) -> None:
    encoded = values.tobytes()
    if predictor == 3:
        native_bytes = values.view(np.uint8).reshape(*values.shape, 4)
        rows = native_bytes[..., ::-1].transpose(0, 2, 1).reshape(values.shape[0], -1)
        differences = np.empty_like(rows)
        differences[:, 0] = rows[:, 0]
        differences[:, 1:] = rows[:, 1:] - rows[:, :-1]
        encoded = differences.tobytes()
    compressed = zlib.compress(encoded)
    entry_count = 15 if declare_nodata else 14
    external_offset = 8 + 2 + entry_count * 12 + 4
    pixel_scale = struct.pack("<3d", 0.25, 0.25, 0.0)
    tiepoint = struct.pack("<6d", 0.0, 0.0, 0.0, -1.0, 40.0, 0.0)
    geo_keys = struct.pack(
        "<16H",
        1, 1, 0, 3,
        1024, 0, 1, 2,
        1025, 0, 1, 1,
        2048, 0, 1, 4326,
    )
    pixel_scale_offset = external_offset
    tiepoint_offset = pixel_scale_offset + len(pixel_scale)
    geo_keys_offset = tiepoint_offset + len(tiepoint)
    metadata_end = geo_keys_offset + len(geo_keys)
    data_offset = metadata_end + (5 if declare_nodata else 0)
    entries = [
        (256, 4, 1, values.shape[1]),
        (257, 4, 1, values.shape[0]),
        (258, 3, 1, 32),
        (259, 3, 1, 8),
        (277, 3, 1, 1),
        (317, 3, 1, predictor),
        (322, 4, 1, values.shape[1]),
        (323, 4, 1, values.shape[0]),
        (324, 4, 1, data_offset),
        (325, 4, 1, len(compressed)),
        (339, 3, 1, 3),
        (33550, 12, 3, pixel_scale_offset),
        (33922, 12, 6, tiepoint_offset),
        (34735, 3, 16, geo_keys_offset),
    ]
    if declare_nodata:
        entries.append((42113, 2, 5, metadata_end))
    entries.sort(key=lambda entry: entry[0])
    directory = struct.pack("<H", entry_count)
    directory += b"".join(struct.pack("<HHII", *entry) for entry in entries)
    directory += struct.pack("<I", 0)
    path.write_bytes(
        b"II" + struct.pack("<HI", 42, 8)
        + directory
        + pixel_scale
        + tiepoint
        + geo_keys
        + (b"-9999" if declare_nodata else b"")
        + compressed
    )
