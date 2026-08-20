"""Selection of native projected coordinate systems for supported Spanish data."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import NoCoverageError
from .roi import BBoxWGS84


@dataclass(frozen=True, slots=True)
class CRSInfo:
    """A supported native UTM coordinate reference system."""

    epsg: int
    name: str
    datum: str
    utm_zone: int


@dataclass(frozen=True, slots=True)
class UTMWorkArea:
    """The part of a WGS84 bounding box assigned to one native UTM CRS."""

    bounds: BBoxWGS84
    crs: CRSInfo


_SUPPORTED_CRS = {
    28: CRSInfo(4083, "REGCAN95 / UTM zone 28N", "REGCAN95", 28),
    29: CRSInfo(25829, "ETRS89 / UTM zone 29N", "ETRS89", 29),
    30: CRSInfo(25830, "ETRS89 / UTM zone 30N", "ETRS89", 30),
    31: CRSInfo(25831, "ETRS89 / UTM zone 31N", "ETRS89", 31),
}

# Western longitude is inclusive; eastern longitude is exclusive except for the
# final boundary. Territory validation is deliberately a separate concern.
_ZONE_LONGITUDE_RANGES = {
    28: (-18.0, -12.0),
    29: (-12.0, -6.0),
    30: (-6.0, 0.0),
    31: (0.0, 6.0),
}


def split_bbox_by_utm_zone(bounds: BBoxWGS84) -> tuple[UTMWorkArea, ...]:
    """Split bounds at supported UTM meridians without claiming land coverage."""

    work_areas: list[UTMWorkArea] = []
    for zone, (zone_west, zone_east) in _ZONE_LONGITUDE_RANGES.items():
        part_west = max(bounds.west, zone_west)
        part_east = min(bounds.east, zone_east)
        if part_east <= part_west:
            continue
        part = BBoxWGS84(part_west, bounds.south, part_east, bounds.north)
        work_areas.append(UTMWorkArea(part, _SUPPORTED_CRS[zone]))
    if not work_areas:
        raise NoCoverageError("ROI does not intersect a supported Spanish UTM zone")
    return tuple(work_areas)
