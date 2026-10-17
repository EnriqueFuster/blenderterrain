"""Provider-neutral projection through installed EPSG definitions."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray
from pyproj import Transformer
from pyproj.exceptions import ProjError

from ..errors import UserInputError
from ..models import ProjectedBounds
from .crs import CRSInfo, ProjectedWorkArea


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
    """Project a WGS84 coordinate to a selected UTM CRS."""

    if crs.utm_zone is None:
        raise UserInputError("The selected CRS is not UTM")
    return project_wgs84(longitude, latitude, crs)


def project_utm_to_wgs84(point: ProjectedPoint, crs: CRSInfo) -> GeographicPoint:
    """Invert a UTM coordinate into WGS84 longitude and latitude."""

    if crs.utm_zone is None:
        raise UserInputError("The selected CRS is not UTM")
    return project_to_wgs84(point, crs)


def project_utm_arrays_to_wgs84(
    eastings: NDArray[np.float64],
    northings: NDArray[np.float64],
    crs: CRSInfo,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Invert equally shaped UTM coordinate arrays."""

    if crs.utm_zone is None:
        raise UserInputError("The selected CRS is not UTM")
    return project_arrays_to_wgs84(eastings, northings, crs)


def project_wgs84(longitude: float, latitude: float, crs: CRSInfo) -> ProjectedPoint:
    """Project one WGS84 coordinate using the selected projected CRS."""

    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise UserInputError("Projection coordinates must be finite")
    if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
        raise UserInputError("Geographic coordinates are outside the supported range")
    try:
        easting, northing = _transformer(4326, crs.epsg).transform(
            longitude, latitude, errcheck=True
        )
    except ProjError as exc:
        raise UserInputError(f"Coordinate cannot be projected to EPSG:{crs.epsg}") from exc
    return ProjectedPoint(float(easting), float(northing), crs.epsg)


def project_arrays_to_wgs84(
    eastings: NDArray[np.float64],
    northings: NDArray[np.float64],
    crs: CRSInfo,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Invert equally shaped projected arrays into WGS84 coordinates."""

    if eastings.shape != northings.shape:
        raise UserInputError("Projected coordinate arrays must have the same shape")
    if not np.isfinite(eastings).all() or not np.isfinite(northings).all():
        raise UserInputError("Projection coordinates must be finite")
    try:
        longitude, latitude = _transformer(crs.epsg, 4326).transform(
            eastings, northings, errcheck=True
        )
    except ProjError as exc:
        raise UserInputError(f"EPSG:{crs.epsg} coordinates cannot be transformed") from exc
    return np.asarray(longitude, dtype=np.float64), np.asarray(latitude, dtype=np.float64)


def project_to_wgs84(point: ProjectedPoint, crs: CRSInfo) -> GeographicPoint:
    """Invert one projected point into WGS84 longitude and latitude."""

    if point.epsg != crs.epsg:
        raise UserInputError("Projected point and CRS EPSG do not match")
    if not math.isfinite(point.easting) or not math.isfinite(point.northing):
        raise UserInputError("Projection coordinates must be finite")
    try:
        longitude, latitude = _transformer(crs.epsg, 4326).transform(
            point.easting, point.northing, errcheck=True
        )
    except ProjError as exc:
        raise UserInputError(f"EPSG:{crs.epsg} coordinate cannot be transformed") from exc
    return GeographicPoint(float(longitude), float(latitude))


def project_work_area_bounds(work_area: ProjectedWorkArea) -> ProjectedBounds:
    """Envelope a geographic work area in its selected projected CRS."""

    bounds = work_area.bounds
    coordinates: list[tuple[float, float]] = []
    for fraction in np.linspace(0.0, 1.0, 33):
        longitude = bounds.west + fraction * (bounds.east - bounds.west)
        latitude = bounds.south + fraction * (bounds.north - bounds.south)
        coordinates.extend(
            (
                (longitude, bounds.south),
                (longitude, bounds.north),
                (bounds.west, latitude),
                (bounds.east, latitude),
            )
        )
    projected = [
        project_wgs84(longitude, latitude, work_area.crs)
        for longitude, latitude in coordinates
    ]
    return ProjectedBounds(
        west=min(point.easting for point in projected),
        south=min(point.northing for point in projected),
        east=max(point.easting for point in projected),
        north=max(point.northing for point in projected),
        epsg=work_area.crs.epsg,
    )


@lru_cache(maxsize=256)
def _transformer(source_epsg: int, target_epsg: int) -> Transformer:
    return Transformer.from_crs(source_epsg, target_epsg, always_xy=True)
