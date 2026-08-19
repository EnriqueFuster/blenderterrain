"""Read-only Phase 0 client for the observed CNIG portal contract."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from blender_terrain.errors import DownloadIntegrityError, ProviderUnavailableError
from blender_terrain.io.atomic import finalize_part, safe_destination
from blender_terrain.io.html_catalog_parser import parse_catalog_page, parse_product_page
from blender_terrain.io.tiff_validation import validate_tiff_signature
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct


BASE_URL = "https://centrodedescargas.cnig.es/CentroDescargas/"
USER_AGENT = "BlenderTerrain/0.0.0 (+https://github.com/EnriqueFuster/blenderterrain)"

PRODUCT_SLUGS = {
    DatasetProduct.MDT02: "modelo-digital-terreno-mdt02-segunda-cobertura",
    DatasetProduct.MDS02: "modelo-digital-superficies-mds02-segunda-cobertura",
}


class _ReadableResponse(Protocol):
    """The streaming portion of an HTTP response needed by this module."""

    def read(self, amount: int = -1) -> bytes:
        """Read up to amount bytes from the response body."""


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
    """Minimal session-based client that performs catalog discovery only."""

    def __init__(self, timeout_seconds: float = 30.0) -> None:
        self._timeout_seconds = timeout_seconds
        self._opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def discover(self, product: DatasetProduct, bbox: BBoxWGS84) -> CatalogPage:
        """Discover the first page of COG resources intersecting a WGS84 bbox."""

        slug = PRODUCT_SLUGS[product]
        page_url = BASE_URL + slug
        product_html = self._request(page_url)
        product_page = parse_product_page(product_html, product)
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

    def download_experiment(
        self,
        item: CatalogItem,
        cache_directory: Path,
        maximum_bytes: int = 1_073_741_824,
    ) -> Path:
        """Download one catalog item through the observed S3 form flow.

        This method is intentionally limited to the Phase 0 experiment. It must
        only be called by an explicit command-line opt-in and does not implement
        retries, resuming, or a persistent cache index.
        """

        if maximum_bytes <= 0:
            raise ValueError("maximum_bytes must be positive")
        if item.file_format.upper() != "COG" or not item.filename.lower().endswith(
            (".tif", ".tiff")
        ):
            raise DownloadIntegrityError("Phase 0 downloader only accepts catalog COG TIFF items")
        cache_directory.mkdir(parents=True, exist_ok=True)
        destination = safe_destination(cache_directory, item.filename)
        if destination.exists():
            raise DownloadIntegrityError(f"Destination already exists: {destination.name}")
        part_path = destination.with_name(destination.name + ".part")
        if part_path.exists():
            raise DownloadIntegrityError(f"Partial download already exists: {part_path.name}")

        detail_url = BASE_URL + f"detalleArchivo?sec={item.sequential_id}"
        form = {
            "secuencial": item.sequential_id,
            "secDescDirLA": "",
            "codSerie": item.product.value,
            "urlCart": "",
            "id_productor": "",
            "codNumMD": "",
            "avisoLimiteFiles": "",
        }
        request = Request(
            BASE_URL + "descargaDirS3",
            data=urlencode(form).encode("utf-8"),
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/octet-stream, image/tiff, */*; q=0.1",
                "Referer": detail_url,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_bytes = int(content_length)
                    except ValueError as exc:
                        raise DownloadIntegrityError(
                            "Download size header is not an integer"
                        ) from exc
                    if declared_bytes > maximum_bytes:
                        raise DownloadIntegrityError(
                            "Declared download size exceeds the experiment limit"
                        )
                content_type = response.headers.get_content_type().lower()
                if content_type == "text/html":
                    raise DownloadIntegrityError(
                        "Download endpoint returned HTML instead of a TIFF resource"
                    )
                self._write_bounded_response(response, part_path, maximum_bytes)
        except (HTTPError, URLError, TimeoutError, socket.timeout, UnicodeError) as exc:
            raise ProviderUnavailableError("CNIG download request failed") from exc

        validate_tiff_signature(part_path)
        finalize_part(part_path, destination)
        return destination

    @staticmethod
    def _write_bounded_response(
        response: _ReadableResponse, part_path: Path, maximum_bytes: int
    ) -> None:
        """Write a response in bounded chunks without promoting incomplete data."""

        total_bytes = 0
        with part_path.open("xb") as stream:
            while chunk := response.read(1_048_576):
                total_bytes += len(chunk)
                if total_bytes > maximum_bytes:
                    raise DownloadIntegrityError("Downloaded size exceeds the experiment limit")
                stream.write(chunk)

    def _request(self, url: str, data: bytes | None = None, referer: str | None = None) -> str:
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
                return response.read().decode(charset, errors="strict")
        except (HTTPError, URLError, TimeoutError, socket.timeout, UnicodeError) as exc:
            raise ProviderUnavailableError(f"CNIG request failed for {url}: {exc}") from exc
