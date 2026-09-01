"""Selection of projected coordinate systems for terrain processing."""

from __future__ import annotations

from dataclasses import dataclass

from ..errors import NoCoverageError, UserInputError
from .roi import BBoxWGS84
from .territory import TerritoryGroup, classify_territory_envelope


@dataclass(frozen=True, slots=True)
class CRSInfo:
    """A projected coordinate reference system supported by terrain processing."""

    epsg: int
    name: str
    datum: str
    utm_zone: int | None


@dataclass(frozen=True, slots=True)
class ProjectedWorkArea:
    """The part of a WGS84 bounding box assigned to one projected CRS."""

    bounds: BBoxWGS84
    crs: CRSInfo


# Compatibility name retained for callers written against the Spanish baseline.
UTMWorkArea = ProjectedWorkArea


_SUPPORTED_CRS = {
    28: CRSInfo(4083, "REGCAN95 / UTM zone 28N", "REGCAN95", 28),
    29: CRSInfo(25829, "ETRS89 / UTM zone 29N", "ETRS89", 29),
    30: CRSInfo(25830, "ETRS89 / UTM zone 30N", "ETRS89", 30),
    31: CRSInfo(25831, "ETRS89 / UTM zone 31N", "ETRS89", 31),
}
LAMBERT93 = CRSInfo(2154, "RGF93 v1 / Lambert-93", "RGF93 v1", None)
_FRANCE_METROPOLITAN_ENVELOPE = BBoxWGS84(-5.5, 41.0, 10.0, 51.5)

# Western longitude is inclusive; eastern longitude is exclusive except for the
# final boundary. Territory validation is deliberately a separate concern.
_ZONE_LONGITUDE_RANGES = {
    28: (-18.0, -12.0),
    29: (-12.0, -6.0),
    30: (-6.0, 0.0),
    31: (0.0, 6.0),
}


def crs_from_epsg(epsg: int) -> CRSInfo:
    """Return a supported projected CRS by canonical EPSG code."""

    if epsg == LAMBERT93.epsg:
        return LAMBERT93
    for crs in _SUPPORTED_CRS.values():
        if crs.epsg == epsg:
            return crs
    raise UserInputError(f"Local raster CRS EPSG:{epsg} is not supported")


def work_area_for_crs(bounds: BBoxWGS84, epsg: int) -> ProjectedWorkArea:
    """Create one work area for a CRS whose supported extent contains the ROI."""

    crs = crs_from_epsg(epsg)
    if epsg == LAMBERT93.epsg and not _contains(_FRANCE_METROPOLITAN_ENVELOPE, bounds):
        raise NoCoverageError("EPSG:2154 processing is limited to metropolitan France and Corsica")
    return ProjectedWorkArea(bounds, crs)


def split_bbox_by_utm_zone(bounds: BBoxWGS84) -> tuple[UTMWorkArea, ...]:
    """Select the datum family and split bounds at supported UTM meridians."""

    territory = classify_territory_envelope(bounds)
    supported_zones = (28,) if territory is TerritoryGroup.CANARY_ISLANDS else (29, 30, 31)
    work_areas: list[UTMWorkArea] = []
    for zone in supported_zones:
        zone_west, zone_east = _ZONE_LONGITUDE_RANGES[zone]
        part_west = max(bounds.west, zone_west)
        part_east = min(bounds.east, zone_east)
        if part_east <= part_west:
            continue
        part = BBoxWGS84(part_west, bounds.south, part_east, bounds.north)
        work_areas.append(UTMWorkArea(part, _SUPPORTED_CRS[zone]))
    if not work_areas:
        raise NoCoverageError("ROI does not intersect a supported Spanish UTM zone")
    return tuple(work_areas)


def split_bbox_by_wgs84_utm_zone(bounds: BBoxWGS84) -> tuple[UTMWorkArea, ...]:
    """Split a non-polar ROI into standard WGS84 UTM work areas."""

    if bounds.south < -80.0 or bounds.north > 84.0:
        raise NoCoverageError("WGS84 UTM processing is limited to 80°S-84°N")
    first_zone = min(60, int((bounds.west + 180.0) // 6.0) + 1)
    last_zone = min(60, int((bounds.east + 180.0 - 1e-12) // 6.0) + 1)
    latitude_parts = (
        ((bounds.south, min(bounds.north, 0.0), False),)
        if bounds.north <= 0.0
        else (
            ((max(bounds.south, 0.0), bounds.north, True),)
            if bounds.south >= 0.0
            else ((bounds.south, 0.0, False), (0.0, bounds.north, True))
        )
    )
    work_areas: list[UTMWorkArea] = []
    for zone in range(first_zone, last_zone + 1):
        zone_west = -180.0 + (zone - 1) * 6.0
        zone_east = zone_west + 6.0
        west = max(bounds.west, zone_west)
        east = min(bounds.east, zone_east)
        if east <= west:
            continue
        for south, north, northern in latitude_parts:
            if north <= south:
                continue
            epsg = (32600 if northern else 32700) + zone
            hemisphere = "N" if northern else "S"
            work_areas.append(
                UTMWorkArea(
                    BBoxWGS84(west, south, east, north),
                    CRSInfo(epsg, f"WGS 84 / UTM zone {zone}{hemisphere}", "WGS84", zone),
                )
            )
    return tuple(work_areas)


def _contains(container: BBoxWGS84, candidate: BBoxWGS84) -> bool:
    return (
        container.west <= candidate.west
        and container.south <= candidate.south
        and container.east >= candidate.east
        and container.north >= candidate.north
    )
