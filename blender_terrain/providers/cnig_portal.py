"""Client for the observed CNIG catalog and controlled delivery contracts."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener

from blender_terrain.errors import (
    CatalogContractChanged,
    DownloadAuthorizationRequired,
    DownloadIntegrityError,
    ProviderUnavailableError,
)
from blender_terrain.io.atomic import (
    finalize_part,
    normalized_server_filename,
    safe_destination,
)
from blender_terrain.io.html_catalog_parser import parse_catalog_page, parse_product_page
from blender_terrain.io.tiff_validation import validate_tiff_header
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct


BASE_URL = "https://centrodedescargas.cnig.es/CentroDescargas/"
USER_AGENT = "BlenderTerrain/0.0.0 (+https://github.com/EnriqueFuster/blenderterrain)"



@dataclass(frozen=True, slots=True)
class _ProductContract:
    slug: str
    catalog_series: str


PRODUCT_CONTRACTS = {
    DatasetProduct.MDT02: _ProductContract(
        "modelo-digital-terreno-mdt02-segunda-cobertura", "MDT02"
    ),
    DatasetProduct.MDS02: _ProductContract(
        "modelo-digital-superficies-mds02-segunda-cobertura", "MDS02"
    ),
    DatasetProduct.PNOA_MA: _ProductContract("ortofoto-pnoa-maxima-actualidad", "02211"),
}
DOWNLOAD_INITIALIZATION_MAXIMUM_BYTES = 4_096
ACCEPTED_TIFF_CONTENT_TYPES = {
    "application/octet-stream",
    "application/x-geotiff",
    "binary/octet-stream",
    "image/geotiff",
    "image/tiff",
}


class _ReadableResponse(Protocol):
    """The streaming portion of an HTTP response needed by this module."""

    def read(self, amount: int = -1) -> bytes:
        """Read up to amount bytes from the response body."""


class _NoRedirectHandler(HTTPRedirectHandler):
    """Prevent a download response from redirecting to another endpoint."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class BBoxWGS84:
    """A small rectangular WGS84 query used by the discovery experiment."""

    west: float
    south: float
    east: float
    north: float

    def as_feature_collection(self) -> str:
        """Serialize the bbox as the compact GeoJSON expected by the portal."""

        ring = [
            [self.west, self.south],
            [self.east, self.south],
            [self.east, self.north],
            [self.west, self.north],
            [self.west, self.south],
        ]
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {},
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                }
            ],
        }
        return json.dumps(payload, separators=(",", ":"))


class CNIGPortalClient:
    """Session-based client for CNIG discovery and controlled source delivery."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._cookie_jar = CookieJar()
        self._opener = build_opener(HTTPCookieProcessor(self._cookie_jar))
        self._download_opener = build_opener(
            HTTPCookieProcessor(self._cookie_jar), _NoRedirectHandler()
        )

    def discover(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        """Discover the first page of COG resources intersecting a WGS84 bbox."""

        contract = PRODUCT_CONTRACTS[product]
        page_url = BASE_URL + contract.slug
        product_html = self._request(page_url)
        product_page = parse_product_page(product_html, contract.catalog_series)
        form = {
            "numPagina": "1",
            "codAgr": product_page.catalog_group,
            "codSerie": product_page.catalog_series,
            "coordenadas": bbox.as_feature_collection(),
            "series": "",
            "codComAutonoma": "",
            "codProvincia": "",
            "codIne": "",
            "codTipoArchivo": "COG",
            "codIdiomaInf": "",
            "todaEspania": "",
            "todoMundo": "",
            "idProductor": "",
            "rutaNombre": "",
            "numHoja": "",
            "numHoja25": "",
            "totalArchivos": str(product_page.advertised_total),
            "codSubSerie": "",
            "contieneArc": "",
            "keySearch": "",
            "referCatastral": "",
            "orderBy": "",
        }
        result_html = self._request(
            BASE_URL + "archivosSerie",
            data=urlencode(form).encode("utf-8"),
            referer=page_url,
        )
        return parse_catalog_page(result_html, product)

    def download_item(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
    ) -> Path:
        """Download one explicitly selected item through the first-party CNIG flow."""

        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        if item.file_format.upper() != "COG" or not item.filename.lower().endswith(
            (".tif", ".tiff")
        ):
            raise DownloadIntegrityError("Downloader only accepts catalog COG TIFF items")
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = safe_destination(cache_directory, item.filename)
        if destination.exists():
            raise DownloadIntegrityError(f"Destination already exists: {destination.name}")
        part_path = destination.with_name(destination.name + ".part")
        if part_path.exists():
            raise DownloadIntegrityError(f"Partial download already exists: {part_path.name}")

        detail_url = BASE_URL + f"detalleArchivo?sec={item.sequential_id}"
        self._request(detail_url, maximum_bytes=1_048_576)
        initialization_html = self._request(
            BASE_URL + "initDescargaDir",
            data=urlencode({"secuencial": item.sequential_id}).encode("utf-8"),
            referer=detail_url,
            maximum_bytes=DOWNLOAD_INITIALIZATION_MAXIMUM_BYTES,
        )
        download_sequence = self._parse_download_initialization(
            initialization_html, item.sequential_id
        )
        form = {
            "secuencial": item.sequential_id,
            "secDescDirLA": download_sequence,
            "codSerie": PRODUCT_CONTRACTS[item.product].catalog_series,
            "urlCart": "",
            "id_productor": "",
            "codNumMD": "",
            "avisoLimiteFiles": "",
        }
        request = Request(
            BASE_URL + "descargaDir",
            data=urlencode(form).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": ", ".join(sorted(ACCEPTED_TIFF_CONTENT_TYPES)),
                "Referer": detail_url,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            method="POST",
        )
        try:
            with self._download_opener.open(
                request, timeout=self._timeout_seconds
            ) as response:
                content_length = response.headers.get("Content-Length")
                declared_bytes: int | None = None
                if content_length is not None:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError as exc:
                        raise DownloadIntegrityError(
                            "Download size header is not an integer"
                        ) from exc
                    if declared_bytes > maximum_bytes:
                        raise DownloadIntegrityError(
                            "Declared download size exceeds the configured limit"
                        )
                content_type = response.headers.get_content_type().lower()
                if content_type not in ACCEPTED_TIFF_CONTENT_TYPES:
                    raise DownloadIntegrityError(
                        "Delivery endpoint returned an unsupported content type"
                    )
                response_filename = response.headers.get_filename()
                if response_filename and normalized_server_filename(
                    response_filename
                ) != normalized_server_filename(item.filename):
                    raise DownloadIntegrityError(
                        "Delivered filename does not match the catalog item"
                    )
                written_bytes = self._write_bounded_response(
                    response, part_path, maximum_bytes
                )
                if declared_bytes is not None and written_bytes != declared_bytes:
                    part_path.unlink(missing_ok=True)
                    raise DownloadIntegrityError(
                        "Downloaded size does not match the declared response size"
                    )
        except HTTPError as exc:
            if 300 <= exc.code < 400:
                raise DownloadIntegrityError(
                    "CNIG download endpoint attempted an unauthorized redirect"
                ) from None
            raise ProviderUnavailableError(
                f"CNIG download request returned HTTP {exc.code}"
            ) from None
        except (URLError, TimeoutError, socket.timeout, UnicodeError):
            raise ProviderUnavailableError("CNIG delivery request failed") from None

        try:
            validate_tiff_header(part_path)
        except DownloadIntegrityError:
            part_path.unlink(missing_ok=True)
            raise
        finalize_part(part_path, destination)
        return destination

    @staticmethod
    def _parse_download_initialization(body: str, expected_sequence: str) -> str:
        """Validate the small JSON authorization response used by descargaDir."""

        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CatalogContractChanged(
                "CNIG download initialization did not return JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise CatalogContractChanged("CNIG download initialization has invalid structure")
        license_state = payload.get("muestraLic")
        sequence = payload.get("secuencialDescDir")
        if license_state == "SI":
            raise DownloadAuthorizationRequired(
                "CNIG requires an interactive license confirmation for this resource"
            )
        if license_state != "NO" or sequence != expected_sequence:
            raise CatalogContractChanged(
                "CNIG download initialization returned unexpected authorization data"
            )
        return sequence

    @staticmethod
    def _write_bounded_response(
        response: _ReadableResponse, part_path: Path, maximum_bytes: int
    ) -> int:
        """Write a response in bounded chunks without promoting incomplete data."""

        total_bytes = 0
        try:
            with part_path.open("xb") as stream:
                while chunk := response.read(1_048_576):
                    total_bytes += len(chunk)
                    if total_bytes > maximum_bytes:
                        raise DownloadIntegrityError(
                            "Downloaded size exceeds the configured limit"
                        )
                    stream.write(chunk)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise
        return total_bytes

    def _request(
        self,
        url: str,
        data: bytes | None = None,
        referer: str | None = None,
        maximum_bytes: int | None = None,
    ) -> str:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html, */*; q=0.01",
        }
        if referer:
            headers["Referer"] = referer
            headers["X-Requested-With"] = "XMLHttpRequest"
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                if maximum_bytes is None:
                    body = response.read()
                else:
                    body = response.read(maximum_bytes + 1)
                if maximum_bytes is not None and len(body) > maximum_bytes:
                    raise DownloadIntegrityError("CNIG HTML response exceeds its safety limit")
                return body.decode(charset, errors="strict")
        except (HTTPError, URLError, TimeoutError, socket.timeout, UnicodeError) as exc:
            raise ProviderUnavailableError(f"CNIG request failed for {url}: {exc}") from exc
