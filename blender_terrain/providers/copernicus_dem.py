"""Deterministic source discovery for public Copernicus GLO-30 tiles."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..core.roi import BBoxWGS84

GLO30_BASE_URL = "https://copernicus-dem-30m.s3.amazonaws.com"


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
