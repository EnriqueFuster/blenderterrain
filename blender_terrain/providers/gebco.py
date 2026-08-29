"""Bounded GEBCO 2026 bathymetry and source-quality acquisition."""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import numpy as np
from numpy.typing import NDArray

from ..catalog.models import DatasetKind
from ..catalog.selection import LayerRequest, ProductSelection
from ..core.acquisition import AcquiredRasterLayer
from ..core.delivery import TransferProgress
from ..core.roi import BBoxWGS84
from ..errors import (
    DownloadIntegrityError,
    JobCancelled,
    ProviderUnavailableError,
    RasterFormatError,
)
from ..io.elevation_window import elevation_window_is_valid, write_elevation_window
from ..models import ProjectedBounds

PRODUCT_ID = "GEBCO_2026"
PROVIDER_ID = "gebco"
DAP_BASE = "https://dap.ceda.ac.uk/thredds/dodsC/bodc/gebco/global/gebco_2026"
MAXIMUM_WINDOW_CELLS = 1_048_576
MAXIMUM_RESPONSE_BYTES = 24_000_000
TID_NODATA = 255.0

Fetcher = Callable[[str, int], bytes]


class GebcoAcquirer:
    """Cache aligned GEBCO elevation and TID windows for one confirmed ROI."""

    def __init__(self, fetcher: Fetcher | None = None) -> None:
        self._fetcher = fetcher or _fetch

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
            selection.provider_id != PROVIDER_ID
            or selection.product_id != PRODUCT_ID
            or selection.kind is not DatasetKind.BATHYMETRY
            or request.kind is not DatasetKind.BATHYMETRY
        ):
            raise ValueError("GEBCO acquirer received an incompatible selection")
        urls, shape = gebco_query_urls(roi)
        if shape[0] * shape[1] > MAXIMUM_WINDOW_CELLS:
            raise RasterFormatError("GEBCO source window exceeds the configured cell limit")
        key = hashlib.sha256(
            f"window-v1|{roi.west},{roi.south},{roi.east},{roi.north}".encode("ascii")
        ).hexdigest()[:20]
        target = cache_directory / PROVIDER_ID / PRODUCT_ID / key
        elevation_path = target / "bathymetry.npy"
        tid_path = target / "tid.npy"
        if elevation_window_is_valid(elevation_path) and elevation_window_is_valid(tid_path):
            _report(progress_callback, elevation_path, 0, True)
            _report(progress_callback, tid_path, 1, True)
            return AcquiredRasterLayer(
                PROVIDER_ID,
                PRODUCT_ID,
                DatasetKind.BATHYMETRY,
                (elevation_path,),
                1,
                (tid_path,),
            )
        _discard(elevation_path, tid_path)
        arrays: list[tuple[NDArray[np.float32], ProjectedBounds]] = []
        for index, variable in enumerate(("elevation", "tid")):
            if cancellation_requested():
                raise JobCancelled("GEBCO acquisition was cancelled")
            payload = self._fetcher(urls[variable], MAXIMUM_RESPONSE_BYTES)
            arrays.append(parse_opendap_ascii(payload, variable, shape))
            _report(progress_callback, Path(f"{variable}.ascii"), index, False, len(payload))
        (elevation, bounds), (tid, tid_bounds) = arrays
        if bounds != tid_bounds:
            raise RasterFormatError("GEBCO elevation and TID grids do not align")
        write_elevation_window(elevation_path, elevation, bounds, -32768.0)
        write_elevation_window(tid_path, tid, tid_bounds, TID_NODATA)
        return AcquiredRasterLayer(
            PROVIDER_ID,
            PRODUCT_ID,
            DatasetKind.BATHYMETRY,
            (elevation_path,),
            0,
            (tid_path,),
        )


def gebco_query_urls(roi: BBoxWGS84) -> tuple[dict[str, str], tuple[int, int]]:
    """Build aligned elevation and TID OPeNDAP subset URLs."""

    column_start = max(0, math.floor((roi.west + 180.0) * 240.0))
    column_end = min(86_399, math.ceil((roi.east + 180.0) * 240.0) - 1)
    row_start = max(0, math.floor((roi.south + 90.0) * 240.0))
    row_end = min(43_199, math.ceil((roi.north + 90.0) * 240.0) - 1)
    if row_end < row_start or column_end < column_start:
        raise RasterFormatError("GEBCO ROI does not contain a source cell")
    row_start, row_end = _minimum_pair(row_start, row_end, 43_199)
    column_start, column_end = _minimum_pair(column_start, column_end, 86_399)
    section = f"[{row_start}:1:{row_end}][{column_start}:1:{column_end}]"
    return (
        {
            "elevation": (
                f"{DAP_BASE}/ice_surface_elevation/netcdf/"
                f"GEBCO_2026.nc.ascii?elevation{section}"
            ),
            "tid": (
                f"{DAP_BASE}/type_identifier_grid/netcdf/"
                f"gebco_2026_tid.nc.ascii?tid{section}"
            ),
        },
        (row_end - row_start + 1, column_end - column_start + 1),
    )


def _minimum_pair(start: int, end: int, maximum: int) -> tuple[int, int]:
    if end > start:
        return start, end
    return (start, start + 1) if start < maximum else (start - 1, start)


def parse_opendap_ascii(
    payload: bytes, variable: str, expected_shape: tuple[int, int]
) -> tuple[NDArray[np.float32], ProjectedBounds]:
    """Parse one strict GEBCO ASCII grid response into north-up rows."""

    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        raise DownloadIntegrityError("GEBCO returned non-ASCII data") from exc
    rows, columns = expected_shape
    values = _matrix(text, variable, rows, columns)
    latitudes = _vector(text, f"{variable}.lat", rows)
    longitudes = _vector(text, f"{variable}.lon", columns)
    spacing = 1.0 / 240.0
    if not _regular(latitudes, spacing) or not _regular(longitudes, spacing):
        raise DownloadIntegrityError("GEBCO coordinates are not a regular 15 arc-second grid")
    bounds = ProjectedBounds(
        longitudes[0] - spacing / 2.0,
        latitudes[0] - spacing / 2.0,
        longitudes[-1] + spacing / 2.0,
        latitudes[-1] + spacing / 2.0,
        4326,
    )
    return np.flipud(values).astype(np.float32, copy=False), bounds


def _matrix(text: str, variable: str, rows: int, columns: int) -> NDArray[np.float32]:
    marker = f"{variable}.{variable}[{rows}][{columns}]\n"
    start = text.find(marker)
    if start < 0:
        raise DownloadIntegrityError("GEBCO response has an unexpected array shape")
    lines = text[start + len(marker) :].splitlines()[:rows]
    data = np.empty((rows, columns), dtype=np.float32)
    for row, line in enumerate(lines):
        match = re.fullmatch(rf"\[{row}\],\s*(.+)", line)
        if match is None:
            raise DownloadIntegrityError("GEBCO response contains malformed raster rows")
        try:
            values = tuple(float(value.strip()) for value in match.group(1).split(","))
        except ValueError as exc:
            raise DownloadIntegrityError("GEBCO response contains non-numeric cells") from exc
        if len(values) != columns:
            raise DownloadIntegrityError("GEBCO response row length does not match its shape")
        data[row] = values
    return data


def _vector(text: str, marker: str, length: int) -> NDArray[np.float64]:
    match = re.search(rf"^{re.escape(marker)}\[{length}\]\n([^\n]+)$", text, re.MULTILINE)
    if match is None:
        raise DownloadIntegrityError("GEBCO response is missing coordinate values")
    try:
        values = np.asarray([float(value) for value in match.group(1).split(",")])
    except ValueError as exc:
        raise DownloadIntegrityError("GEBCO coordinates are not numeric") from exc
    if values.shape != (length,):
        raise DownloadIntegrityError("GEBCO coordinate length does not match the grid")
    return values


def _regular(values: NDArray[np.float64], spacing: float) -> bool:
    return len(values) >= 2 and bool(
        np.all(np.isfinite(values))
        and np.allclose(np.diff(values), spacing, rtol=0.0, atol=1e-10)
    )


def _fetch(url: str, maximum_bytes: int) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "dap.ceda.ac.uk":
        raise ValueError("GEBCO URL must use the configured CEDA HTTPS host")
    request = Request(url, headers={"User-Agent": "BlenderTerrain/0.4"})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = cast(bytes, response.read(maximum_bytes + 1))
            if len(payload) > maximum_bytes:
                raise DownloadIntegrityError("GEBCO response exceeds the configured byte limit")
            return payload
        except HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise ProviderUnavailableError(
                    f"GEBCO source returned HTTP {exc.code}"
                ) from None
        except (URLError, TimeoutError, ConnectionError, OSError):
            pass
        if attempt < 2:
            time.sleep(0.25 * (2**attempt))
    raise ProviderUnavailableError("GEBCO source failed after 3 attempts")


def _discard(*paths: Path) -> None:
    for path in paths:
        path.unlink(missing_ok=True)
        path.with_suffix(path.suffix + ".json").unlink(missing_ok=True)


def _report(
    callback: Callable[[TransferProgress], None] | None,
    path: Path,
    index: int,
    cached: bool,
    size: int | None = None,
) -> None:
    if callback is not None:
        written = path.stat().st_size if size is None and path.is_file() else size or 0
        callback(
            TransferProgress(
                DatasetKind.BATHYMETRY.value,
                index,
                2,
                path.name,
                written,
                written if cached else None,
                cached,
            )
        )
