"""Forward and inverse projection for the supported northern UTM zones."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

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


@dataclass(frozen=True, slots=True)
class GeographicPoint:
    """One longitude/latitude coordinate in degrees."""

    longitude: float
    latitude: float


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


def project_utm_to_wgs84(point: ProjectedPoint, crs: CRSInfo) -> GeographicPoint:
    """Invert a supported northern GRS80 UTM coordinate into longitude/latitude."""

    if point.epsg != crs.epsg:
        raise UserInputError("Projected point and CRS EPSG do not match")
    longitude, latitude = project_utm_arrays_to_wgs84(
        np.asarray([point.easting], dtype=np.float64),
        np.asarray([point.northing], dtype=np.float64),
        crs,
    )
    return GeographicPoint(float(longitude[0]), float(latitude[0]))


def project_utm_arrays_to_wgs84(
    eastings: NDArray[np.float64],
    northings: NDArray[np.float64],
    crs: CRSInfo,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Invert equally shaped UTM coordinate arrays without per-sample Python calls."""

    if eastings.shape != northings.shape:
        raise UserInputError("Projected coordinate arrays must have the same shape")
    if not np.isfinite(eastings).all() or not np.isfinite(northings).all():
        raise UserInputError("Projection coordinates must be finite")
    flattening = 1.0 / _GRS80_INVERSE_FLATTENING
    eccentricity_squared = flattening * (2.0 - flattening)
    second_eccentricity_squared = eccentricity_squared / (1.0 - eccentricity_squared)
    meridional_arc = northings / _UTM_SCALE_FACTOR
    mu = meridional_arc / (
        _GRS80_SEMI_MAJOR_AXIS
        * (
            1.0
            - eccentricity_squared / 4.0
            - 3.0 * eccentricity_squared**2 / 64.0
            - 5.0 * eccentricity_squared**3 / 256.0
        )
    )
    root = math.sqrt(1.0 - eccentricity_squared)
    e1 = (1.0 - root) / (1.0 + root)
    footprint = (
        mu
        + (3.0 * e1 / 2.0 - 27.0 * e1**3 / 32.0) * np.sin(2.0 * mu)
        + (21.0 * e1**2 / 16.0 - 55.0 * e1**4 / 32.0) * np.sin(4.0 * mu)
        + 151.0 * e1**3 / 96.0 * np.sin(6.0 * mu)
        + 1097.0 * e1**4 / 512.0 * np.sin(8.0 * mu)
    )
    sine = np.sin(footprint)
    cosine = np.cos(footprint)
    tangent = np.tan(footprint)
    radius_prime_vertical = _GRS80_SEMI_MAJOR_AXIS / np.sqrt(
        1.0 - eccentricity_squared * sine**2
    )
    radius_meridian = (
        _GRS80_SEMI_MAJOR_AXIS
        * (1.0 - eccentricity_squared)
        / (1.0 - eccentricity_squared * sine**2) ** 1.5
    )
    tangent_squared = tangent**2
    eta_squared = second_eccentricity_squared * cosine**2
    d = (eastings - _UTM_FALSE_EASTING) / (
        radius_prime_vertical * _UTM_SCALE_FACTOR
    )
    latitude = footprint - (radius_prime_vertical * tangent / radius_meridian) * (
        d**2 / 2.0
        - (
            5.0
            + 3.0 * tangent_squared
            + 10.0 * eta_squared
            - 4.0 * eta_squared**2
            - 9.0 * second_eccentricity_squared
        )
        * d**4
        / 24.0
        + (
            61.0
            + 90.0 * tangent_squared
            + 298.0 * eta_squared
            + 45.0 * tangent_squared**2
            - 252.0 * second_eccentricity_squared
            - 3.0 * eta_squared**2
        )
        * d**6
        / 720.0
    )
    longitude = (
        d
        - (1.0 + 2.0 * tangent_squared + eta_squared) * d**3 / 6.0
        + (
            5.0
            - 2.0 * eta_squared
            + 28.0 * tangent_squared
            - 3.0 * eta_squared**2
            + 8.0 * second_eccentricity_squared
            + 24.0 * tangent_squared**2
        )
        * d**5
        / 120.0
    ) / cosine
    central_meridian = crs.utm_zone * 6.0 - 183.0
    return central_meridian + np.degrees(longitude), np.degrees(latitude)


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
