"""Explicit OSTN15 horizontal transformations for British raster adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..errors import DownloadIntegrityError, NoCoverageError, ProviderUnavailableError

OSTN15_SHA256 = "5d6ed64d2119952c4c559fa1fccbc594b6520fc3ec3ef2fc10be13202c4384fa"


class BritishGridTransform:
    """Transform ETRS89-like geographic coordinates to BNG without ballpark fallback.

    WGS84 ROI coordinates are treated as ETRS89, as elsewhere in the toolkit.
    This is not an epoch-aware survey transformation. Raster heights are unchanged.
    """

    def __init__(self, grid_path: Path) -> None:
        if not grid_path.is_file():
            raise ProviderUnavailableError("The required OSTN15 grid is missing")
        if hashlib.sha256(grid_path.read_bytes()).hexdigest() != OSTN15_SHA256:
            raise DownloadIntegrityError("OSTN15 grid checksum does not match")
        # Lazy import keeps the portable package importable in Blender before packaging.
        try:
            from pyproj import Transformer
        except ImportError as exc:
            raise ProviderUnavailableError("British raster transforms require PyProj") from exc
        path = grid_path.resolve().as_posix()
        self._transformer = Transformer.from_pipeline(
            "+proj=pipeline +step +proj=unitconvert +xy_in=deg +xy_out=rad "
            f'+step +inv +proj=hgridshift +grids="{path}" '
            "+step +proj=tmerc +lat_0=49 +lon_0=-2 +k=0.9996012717 "
            "+x_0=400000 +y_0=-100000 +ellps=airy"
        )

    def forward(self, longitude: Any, latitude: Any) -> tuple[Any, Any]:
        """Return BNG eastings/northings for scalars or arrays, rejecting grid gaps."""

        return self._apply(longitude, latitude, "FORWARD")

    def inverse(self, easting: Any, northing: Any) -> tuple[Any, Any]:
        """Return longitude/latitude without modifying vertical coordinates."""

        return self._apply(easting, northing, "INVERSE")

    def _apply(self, x: Any, y: Any, direction: str) -> tuple[Any, Any]:
        from pyproj.exceptions import ProjError

        try:
            result = self._transformer.transform(x, y, direction=direction, errcheck=True)
        except ProjError as exc:
            raise NoCoverageError(
                "Coordinates fall outside the OSTN15 transformation grid"
            ) from exc
        return result[0], result[1]
