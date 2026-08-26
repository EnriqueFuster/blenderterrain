"""Client for the observed CNIG catalog and controlled delivery contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener

from ..core.roi import BBoxWGS84
from ..errors import (
    CatalogContractChanged,
    DownloadAuthorizationRequired,
    DownloadIntegrityError,
    ProviderUnavailableError,
)
from ..io.atomic import (
    finalize_part,
    normalized_server_filename,
    safe_destination,
)
from ..io.html_catalog_parser import parse_catalog_page, parse_product_page
from ..io.tiff_validation import validate_tiff_header
from ..models import CatalogItem, CatalogPage, DatasetProduct, ProductPage

BASE_URL = "https://centrodedescargas.cnig.es/CentroDescargas/"
USER_AGENT = "BlenderTerrain/0.2.0 (+https://github.com/EnriqueFuster/blenderterrain)"



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
MAX_CATALOG_PAGES = 1_000
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


def _bbox_feature_collection(bounds: BBoxWGS84) -> str:
    """Serialize bounds using the compact GeoJSON contract expected by CNIG."""

    ring = [[longitude, latitude] for longitude, latitude in bounds.polygon_ring()]
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

        page_url, product_page = self._prepare_discovery(product)
        return self._request_catalog_page(product, bbox, page_url, product_page, 1)

    def discover_all(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        """Discover and deduplicate every advertised catalog page for one ROI."""

        page_url, product_page = self._prepare_discovery(product)
        unique_items: dict[str, CatalogItem] = {}
        expected_total: int | None = None
        previous_page_ids: tuple[str, ...] | None = None
        for page_number in range(1, MAX_CATALOG_PAGES + 1):
            page = self._request_catalog_page(
                product, bbox, page_url, product_page, page_number
            )
            if expected_total is None:
                expected_total = page.total_items
            elif page.total_items != expected_total:
                raise CatalogContractChanged("CNIG catalog total changed during pagination")
            if expected_total == 0:
                return CatalogPage(0, ())

            page_ids = tuple(item.sequential_id for item in page.items)
            previous_unique_count = len(unique_items)
            for item in page.items:
                existing = unique_items.get(item.sequential_id)
                if existing is not None and existing != item:
                    raise CatalogContractChanged(
                        "CNIG catalog returned conflicting rows for one sequential identifier"
                    )
                unique_items[item.sequential_id] = item
            if page_ids == previous_page_ids or len(unique_items) == previous_unique_count:
                raise CatalogContractChanged("CNIG catalog made no progress during pagination")
            previous_page_ids = page_ids
            if len(unique_items) >= expected_total:
                if len(unique_items) != expected_total:
                    raise CatalogContractChanged(
                        "CNIG catalog returned more unique rows than advertised"
                    )
                return CatalogPage(expected_total, tuple(unique_items.values()))
        raise CatalogContractChanged("CNIG catalog pagination exceeded its safety limit")

    def _prepare_discovery(self, product: DatasetProduct) -> tuple[str, ProductPage]:
        contract = PRODUCT_CONTRACTS[product]
        page_url = BASE_URL + contract.slug
        product_html = self._request(page_url)
        product_page = parse_product_page(product_html, contract.catalog_series)
        return page_url, product_page

    def _request_catalog_page(
        self,
        product: DatasetProduct,
        bbox: BBoxWGS84,
        page_url: str,
        product_page: ProductPage,
        page_number: int,
    ) -> CatalogPage:
        """Request one page using fields parsed from the current product form."""

        form = {
            "numPagina": str(page_number),
            "codAgr": product_page.catalog_group,
            "codSerie": product_page.catalog_series,
            "coordenadas": _bbox_feature_collection(bbox),
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
        progress_callback: Callable[[int, int | None], None] | None = None,
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
                    response,
                    part_path,
                    maximum_bytes,
                    declared_bytes,
                    progress_callback,
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
        except (URLError, TimeoutError, UnicodeError):
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
        if (
            license_state != "NO"
            or not isinstance(sequence, str)
            or sequence != expected_sequence
        ):
            raise CatalogContractChanged(
                "CNIG download initialization returned unexpected authorization data"
            )
        return sequence

    @staticmethod
    def _write_bounded_response(
        response: _ReadableResponse,
        part_path: Path,
        maximum_bytes: int,
        expected_bytes: int | None = None,
        progress_callback: Callable[[int, int | None], None] | None = None,
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
                    if progress_callback is not None:
                        progress_callback(total_bytes, expected_bytes)
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
                return bytes(body).decode(charset, errors="strict")
        except (HTTPError, URLError, TimeoutError, UnicodeError) as exc:
            raise ProviderUnavailableError(f"CNIG request failed for {url}: {exc}") from exc
