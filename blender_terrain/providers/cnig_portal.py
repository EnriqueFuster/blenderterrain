"""Read-only Phase 0 client for the observed CNIG portal contract."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from blender_terrain.errors import ProviderUnavailableError
from blender_terrain.io.html_catalog_parser import parse_catalog_page, parse_product_page
from blender_terrain.models import CatalogPage, DatasetProduct


BASE_URL = "https://centrodedescargas.cnig.es/CentroDescargas/"
USER_AGENT = "BlenderTerrain/0.0.0 (+https://github.com/EnriqueFuster/blenderterrain)"

PRODUCT_SLUGS = {
    DatasetProduct.MDT02: "modelo-digital-terreno-mdt02-segunda-cobertura",
    DatasetProduct.MDS02: "modelo-digital-superficies-mds02-segunda-cobertura",
}


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
