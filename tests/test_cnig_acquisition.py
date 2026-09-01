from __future__ import annotations

from pathlib import Path

from blender_terrain.catalog import DatasetKind, LayerRequest, ProductSelection, SelectionMode
from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct
from blender_terrain.providers.cnig_acquisition import CnigElevationAcquirer


class FakeCnigClient:
    def __init__(self) -> None:
        self.discovered: list[tuple[DatasetProduct, BBoxWGS84]] = []
        self.downloaded: list[CatalogItem] = []

    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        self.discovered.append((product, bbox))
        item = CatalogItem(product, "source-ETRS89-HU30-elevation.tif", "COG", "42")
        return CatalogPage(1, (item,))

    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
        progress_callback=None,
    ) -> Path:
        self.downloaded.append(item)
        cache_directory.mkdir(parents=True, exist_ok=True)
        path = cache_directory / item.filename
        path.write_bytes(b"II*\x00\x08\x00\x00\x00\x00")
        if progress_callback is not None:
            progress_callback(9, 9)
        return path


def test_adapts_cnig_catalog_delivery_to_an_acquired_raster_layer(tmp_path: Path) -> None:
    client = FakeCnigClient()
    selection = ProductSelection(
        "ign_cnig", "MDT02", DatasetKind.DTM, SelectionMode.MANUAL, True
    )
    roi = BBoxWGS84(-0.39, 39.46, -0.37, 39.48)

    result = CnigElevationAcquirer(client).acquire(
        selection,
        LayerRequest(DatasetKind.DTM, 10.0),
        roi,
        tmp_path,
    )

    assert client.discovered == [(DatasetProduct.MDT02, roi)]
    assert client.downloaded[0].sequential_id == "42"
    assert result.provider_id == "ign_cnig"
    assert result.kind is DatasetKind.DTM
    assert result.paths[0].parts[-3:] == (
        "ign_cnig",
        "MDT02",
        "source-ETRS89-HU30-elevation.tif",
    )
