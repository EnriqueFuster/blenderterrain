"""Provider-neutral coordinate reference systems for terrain processing."""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from functools import lru_cache

from pyproj import CRS
from pyproj.exceptions import CRSError

from ..errors import NoCoverageError, UserInputError
from .roi import BBoxWGS84


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


UTMWorkArea = ProjectedWorkArea


@lru_cache(maxsize=128)
def crs_from_epsg(epsg: int) -> CRSInfo:
    """Resolve any projected metric EPSG coordinate system."""

    try:
        crs = CRS.from_epsg(epsg)
    except CRSError as exc:
        raise UserInputError(f"Unknown coordinate reference system EPSG:{epsg}") from exc
    if not crs.is_projected:
        raise UserInputError(f"EPSG:{epsg} is not a projected coordinate system")
    if not crs.axis_info or any(
        axis.unit_conversion_factor is None
        or abs(axis.unit_conversion_factor - 1.0) > 1e-12
        for axis in crs.axis_info[:2]
    ):
        raise UserInputError(f"EPSG:{epsg} must use metre-based horizontal axes")
    datum = crs.datum.name if crs.datum is not None else "Unknown"
    return CRSInfo(epsg, crs.name, datum, _utm_zone(crs.utm_zone))


def work_area_for_crs(bounds: BBoxWGS84, epsg: int) -> ProjectedWorkArea:
    """Create one work area using a provider-selected projected CRS."""

    return ProjectedWorkArea(bounds, crs_from_epsg(epsg))


@lru_cache(maxsize=128)
def canonical_xy_epsg(declared_epsg: int) -> int:
    """Return an equivalent EPSG whose projected axes are easting then northing."""

    try:
        crs = CRS.from_epsg(declared_epsg)
    except CRSError:
        return declared_epsg
    directions = tuple(axis.direction.lower() for axis in crs.axis_info[:2])
    if directions != ("north", "east"):
        return declared_epsg
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        equivalent = CRS.from_proj4(crs.to_proj4()).to_epsg()
    return declared_epsg if equivalent is None else equivalent


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
            work_areas.append(
                UTMWorkArea(BBoxWGS84(west, south, east, north), crs_from_epsg(epsg))
            )
    return tuple(work_areas)


def _utm_zone(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(character for character in value if character.isdigit())
    return int(digits) if digits else None
