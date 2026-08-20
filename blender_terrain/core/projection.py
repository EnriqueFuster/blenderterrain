"""Dependency-free forward projection for the supported northern UTM zones."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..errors import UserInputError
from ..models import ProjectedBounds
from .crs import CRSInfo, UTMWorkArea

_GRS80_SEMI_MAJOR_AXIS = 6_378_137.0
_GRS80_INVERSE_FLATTENING = 298.257_222_101
_UTM_SCALE_FACTOR = 0.9996
_UTM_FALSE_EASTING = 500_000.0


@dataclass(frozen=True, slots=True)
class ProjectedPoint:
    """One easting/northing coordinate in a known projected CRS."""

    easting: float
    northing: float
    epsg: int


def project_wgs84_to_utm(longitude: float, latitude: float, crs: CRSInfo) -> ProjectedPoint:
    """Project a WGS84-like coordinate to a supported GRS80 northern UTM CRS."""

    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise UserInputError("Projection coordinates must be finite")
    if not -180.0 <= longitude <= 180.0 or not 0.0 <= latitude <= 84.0:
        raise UserInputError("Supported UTM projection requires northern latitude 0 to 84")
    central_meridian = crs.utm_zone * 6.0 - 183.0
    if abs(longitude - central_meridian) > 6.0:
        raise UserInputError("Coordinate is too far from the selected UTM zone")

    flattening = 1.0 / _GRS80_INVERSE_FLATTENING
    eccentricity_squared = flattening * (2.0 - flattening)
    second_eccentricity_squared = eccentricity_squared / (1.0 - eccentricity_squared)
    latitude_radians = math.radians(latitude)
    longitude_delta = math.radians(longitude - central_meridian)
    sine_latitude = math.sin(latitude_radians)
    cosine_latitude = math.cos(latitude_radians)
    tangent_latitude = math.tan(latitude_radians)
    radius_prime_vertical = _GRS80_SEMI_MAJOR_AXIS / math.sqrt(
        1.0 - eccentricity_squared * sine_latitude**2
    )
    tangent_squared = tangent_latitude**2
    eta_squared = second_eccentricity_squared * cosine_latitude**2
    a_term = cosine_latitude * longitude_delta
    meridional_arc = _meridional_arc(latitude_radians, eccentricity_squared)

    easting = _UTM_FALSE_EASTING + _UTM_SCALE_FACTOR * radius_prime_vertical * (
        a_term
        + (1.0 - tangent_squared + eta_squared) * a_term**3 / 6.0
        + (
            5.0
            - 18.0 * tangent_squared
            + tangent_squared**2
            + 72.0 * eta_squared
            - 58.0 * second_eccentricity_squared
        )
        * a_term**5
        / 120.0
    )
    northing = _UTM_SCALE_FACTOR * (
        meridional_arc
        + radius_prime_vertical
        * tangent_latitude
        * (
            a_term**2 / 2.0
            + (5.0 - tangent_squared + 9.0 * eta_squared + 4.0 * eta_squared**2)
            * a_term**4
            / 24.0
            + (
                61.0
                - 58.0 * tangent_squared
                + tangent_squared**2
                + 600.0 * eta_squared
                - 330.0 * second_eccentricity_squared
            )
            * a_term**6
            / 720.0
        )
    )
    return ProjectedPoint(easting, northing, crs.epsg)


def project_work_area_bounds(work_area: UTMWorkArea) -> ProjectedBounds:
    """Envelope a rectangular work area using corners and central-meridian extrema."""

    bounds = work_area.bounds
    coordinates = list(bounds.polygon_ring()[:-1])
    central_meridian = work_area.crs.utm_zone * 6.0 - 183.0
    if bounds.west < central_meridian < bounds.east:
        coordinates.extend(
            ((central_meridian, bounds.south), (central_meridian, bounds.north))
        )
    projected = [
        project_wgs84_to_utm(longitude, latitude, work_area.crs)
        for longitude, latitude in coordinates
    ]
    return ProjectedBounds(
        west=min(point.easting for point in projected),
        south=min(point.northing for point in projected),
        east=max(point.easting for point in projected),
        north=max(point.northing for point in projected),
        epsg=work_area.crs.epsg,
    )


def _meridional_arc(latitude_radians: float, eccentricity_squared: float) -> float:
    eccentricity_fourth = eccentricity_squared**2
    eccentricity_sixth = eccentricity_squared**3
    latitude_coefficient = (
        1.0
        - eccentricity_squared / 4.0
        - 3.0 * eccentricity_fourth / 64.0
        - 5.0 * eccentricity_sixth / 256.0
    )
    sine_two_coefficient = (
        3.0 * eccentricity_squared / 8.0
        + 3.0 * eccentricity_fourth / 32.0
        + 45.0 * eccentricity_sixth / 1024.0
    )
    sine_four_coefficient = (
        15.0 * eccentricity_fourth / 256.0
        + 45.0 * eccentricity_sixth / 1024.0
    )
    return _GRS80_SEMI_MAJOR_AXIS * (
        latitude_coefficient * latitude_radians
        - sine_two_coefficient * math.sin(2.0 * latitude_radians)
        + sine_four_coefficient * math.sin(4.0 * latitude_radians)
        - 35.0 * eccentricity_sixth / 3072.0 * math.sin(6.0 * latitude_radians)
    )
