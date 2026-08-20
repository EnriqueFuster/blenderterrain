"""Bounded WMS 1.3 client for PNOA Maximum Actuality imagery."""

from __future__ import annotations

from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..errors import (
    DownloadIntegrityError,
    ProviderContractChanged,
    ProviderUnavailableError,
)
from ..io.atomic import finalize_part, safe_destination
from ..io.png_validation import validate_png
from ..io.wms_capabilities import WMSCapabilities, parse_wms_capabilities
from ..models import ProjectedBounds

WMS_URL = "https://www.ign.es/wms-inspire/pnoa-ma"
PNOA_LAYER = "OI.OrthoimageCoverage"
USER_AGENT = "BlenderTerrain/0.0.0 (+https://github.com/EnriqueFuster/blenderterrain)"
CAPABILITIES_MAXIMUM_BYTES = 1_048_576
CONTROL_TESTED_EPSG = 25830


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class PNOAWMSClient:
    """Fetch capabilities and projected PNG texture windows from official PNOA."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(_NoRedirectHandler())
        self._capabilities: WMSCapabilities | None = None

    def capabilities(self) -> WMSCapabilities:
        """Return the validated current service contract."""

        if self._capabilities is not None:
            return self._capabilities
        query = urlencode({"SERVICE": "WMS", "REQUEST": "GetCapabilities"})
        request = Request(f"{WMS_URL}?{query}", headers={"User-Agent": USER_AGENT})
        body, content_type = self._read_response(request, CAPABILITIES_MAXIMUM_BYTES)
        if content_type not in {"application/xml", "text/xml"}:
            raise ProviderContractChanged("WMS capabilities returned an unexpected content type")
        self._capabilities = parse_wms_capabilities(body, PNOA_LAYER)
        return self._capabilities

    def download_png(
        self,
        bounds: ProjectedBounds,
        width: int,
        height: int,
        cache_directory: Path,
        filename: str,
    ) -> Path:
        """Download one north-up projected map image into the local cache."""

        capabilities = self.capabilities()
        _validate_map_request(bounds, width, height, capabilities)
        if Path(filename).suffix.lower() != ".png":
            raise DownloadIntegrityError("WMS PNG destination must use a .png extension")
        destination = safe_destination(cache_directory, filename)
        cache_directory.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise DownloadIntegrityError(f"Destination already exists: {destination.name}")
        part_path = destination.with_name(destination.name + ".part")
        if part_path.exists():
            raise DownloadIntegrityError(f"Partial download already exists: {part_path.name}")

        request = Request(
            self._map_url(bounds, width, height),
            headers={"User-Agent": USER_AGENT, "Accept": "image/png"},
        )
        maximum_bytes = width * height * 4 + 1_048_576
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                if response.headers.get_content_type().lower() != "image/png":
                    raise DownloadIntegrityError("WMS GetMap returned an unexpected content type")
                total = 0
                with part_path.open("xb") as stream:
                    while chunk := response.read(1_048_576):
                        total += len(chunk)
                        if total > maximum_bytes:
                            raise DownloadIntegrityError("WMS PNG exceeds its expected size limit")
                        stream.write(chunk)
        except HTTPError as exc:
            raise ProviderUnavailableError(f"PNOA WMS returned HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            raise ProviderUnavailableError("PNOA WMS request failed") from None

        validate_png(part_path, width, height)
        finalize_part(part_path, destination)
        return destination

    @staticmethod
    def _map_url(bounds: ProjectedBounds, width: int, height: int) -> str:
        query = urlencode(
            {
                "SERVICE": "WMS",
                "VERSION": "1.3.0",
                "REQUEST": "GetMap",
                "LAYERS": PNOA_LAYER,
                "STYLES": "",
                "CRS": f"EPSG:{bounds.epsg}",
                "BBOX": f"{bounds.west},{bounds.south},{bounds.east},{bounds.north}",
                "WIDTH": str(width),
                "HEIGHT": str(height),
                "FORMAT": "image/png",
                "TRANSPARENT": "FALSE",
            }
        )
        return f"{WMS_URL}?{query}"

    def _read_response(self, request: Request, maximum_bytes: int) -> tuple[bytes, str]:
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                body = response.read(maximum_bytes + 1)
                content_type = response.headers.get_content_type().lower()
        except HTTPError as exc:
            raise ProviderUnavailableError(f"PNOA WMS returned HTTP {exc.code}") from None
        except (URLError, TimeoutError):
            raise ProviderUnavailableError("PNOA WMS request failed") from None
        if len(body) > maximum_bytes:
            raise ProviderContractChanged("WMS capabilities response exceeds its size limit")
        return body, content_type


def _validate_map_request(
    bounds: ProjectedBounds,
    width: int,
    height: int,
    capabilities: WMSCapabilities,
) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("WMS image dimensions must be positive")
    if width > capabilities.max_width or height > capabilities.max_height:
        raise ValueError("WMS image dimensions exceed the advertised service limit")
    if bounds.epsg != CONTROL_TESTED_EPSG:
        raise ValueError("Only the control-tested EPSG:25830 WMS axis order is supported")
    if f"EPSG:{bounds.epsg}" not in capabilities.crs:
        raise ProviderContractChanged("WMS layer no longer supports the requested CRS")
