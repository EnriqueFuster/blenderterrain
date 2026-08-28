"""Deterministic source discovery for public Copernicus GLO-30 tiles."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import JobCancelled
from ..io.http_download import download_public_tiff

GLO30_BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
GLO30_PRODUCT_ID = "COPERNICUS_GLO30_2021"
GLO30_PROVIDER_ID = "copernicus_dem"


@dataclass(frozen=True, slots=True)
class Glo30Tile:
    """One one-degree GLO-30 source tile and its exact geographic bounds."""

    longitude: int
    latitude: int
    url: str

    @property
    def bounds(self) -> BBoxWGS84:
        return BBoxWGS84(
            float(self.longitude),
            float(self.latitude),
            float(self.longitude + 1),
            float(self.latitude + 1),
        )

    @property
    def filename(self) -> str:
        return Path(urlsplit(self.url).path).name


class CopernicusGlo30Acquirer:
    """Acquire the GLO-30 tiles required by one confirmed DSM selection."""

    def __init__(self, maximum_tile_bytes: int = 100_000_000) -> None:
        if maximum_tile_bytes <= 0:
            raise ValueError("Maximum tile size must be positive")
        self.maximum_tile_bytes = maximum_tile_bytes

    def acquire(
        self,
        selection: ProductSelection,
        request: LayerRequest,
        roi: BBoxWGS84,
        cache_directory: Path,
        progress_callback: Callable[[TransferProgress], None] | None = None,
        cancellation_requested: Callable[[], bool] = lambda: False,
    ) -> AcquiredRasterLayer:
        if (
            selection.provider_id != GLO30_PROVIDER_ID
            or selection.product_id != GLO30_PRODUCT_ID
            or selection.kind is not DatasetKind.DSM
            or request.kind is not DatasetKind.DSM
        ):
            raise ValueError("GLO-30 acquirer received an incompatible selection")
        tiles = glo30_tiles_for_roi(roi)
        target = cache_directory / selection.provider_id / selection.product_id
        paths: list[Path] = []
        cached_count = 0
        for index, tile in enumerate(tiles):
            if cancellation_requested():
                raise JobCancelled("GLO-30 acquisition was cancelled")

            def report(
                written: int,
                expected: int | None,
                *,
                _tile: Glo30Tile = tile,
                _index: int = index,
            ) -> None:
                if progress_callback is not None:
                    progress_callback(
                        TransferProgress(
                            DatasetKind.DSM.value,
                            _index,
                            len(tiles),
                            _tile.filename,
                            written,
                            expected,
                        )
                    )

            asset = download_public_tiff(
                tile.url,
                target,
                tile.filename,
                maximum_bytes=self.maximum_tile_bytes,
                progress_callback=report,
                cancelled=cancellation_requested,
            )
            paths.append(asset.path)
            cached_count += int(asset.cached)
            if asset.cached and progress_callback is not None:
                progress_callback(
                    TransferProgress(
                        DatasetKind.DSM.value,
                        index,
                        len(tiles),
                        tile.filename,
                        asset.bytes,
                        asset.bytes,
                        cached=True,
                    )
                )
        return AcquiredRasterLayer(
            selection.provider_id,
            selection.product_id,
            selection.kind,
            tuple(paths),
            cached_count,
        )


def glo30_tiles_for_roi(roi: BBoxWGS84) -> tuple[Glo30Tile, ...]:
    """Return north-to-south, west-to-east GLO-30 tiles intersecting a ROI."""

    west = math.floor(roi.west)
    east = math.floor(math.nextafter(roi.east, -math.inf))
    south = math.floor(roi.south)
    north = math.floor(math.nextafter(roi.north, -math.inf))
    return tuple(
        _tile(longitude, latitude)
        for latitude in range(north, south - 1, -1)
        for longitude in range(west, east + 1)
    )


def _tile(longitude: int, latitude: int) -> Glo30Tile:
    tile_id = (
        f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}_00_"
        f"{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}_00"
    )
    name = f"Copernicus_DSM_COG_10_{tile_id}_DEM"
    return Glo30Tile(
        longitude=longitude,
        latitude=latitude,
        url=f"{GLO30_BASE_URL}/{name}/{name}.tif",
    )
