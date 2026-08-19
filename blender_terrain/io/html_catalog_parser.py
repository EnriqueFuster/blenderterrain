"""Strict parsers for the observed CNIG catalog HTML contract."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from blender_terrain.errors import CatalogContractChanged
from blender_terrain.models import CatalogItem, CatalogPage, DatasetProduct, ProductPage


def _attributes(attributes: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name: value or "" for name, value in attributes}


def _normalized_text(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


class _ProductPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_fields: dict[str, str] = {}
        self.formats: list[str] = []
        self._in_format_select = False
        self._current_option_value: str | None = None

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        attrs = _attributes(attributes)
        if tag == "input":
            field_name = attrs.get("id") or attrs.get("name")
            if field_name:
                self.hidden_fields[field_name] = attrs.get("value", "")
        elif tag == "select" and (attrs.get("id") or attrs.get("name")) == "comboTipoArchSerie":
            self._in_format_select = True
        elif tag == "option" and self._in_format_select:
            self._current_option_value = attrs.get("value")

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._in_format_select = False
        elif tag == "option" and self._current_option_value is not None:
            if self._current_option_value:
                self.formats.append(self._current_option_value)
            self._current_option_value = None


class _CatalogResultsParser(HTMLParser):
    _LABEL_PREFIXES = {
        "Nombre": "Archivo",
        "Formato": "Formato",
        "Fecha": "Fecha descarga",
        "Escala fotograma": "Escala",
        "Tamaño\xa0(MB)": "MB",
    }

    def __init__(self, product: DatasetProduct) -> None:
        super().__init__(convert_charrefs=True)
        self.product = product
        self.total_items: int | None = None
        self.items: list[CatalogItem] = []
        self._in_result_row = False
        self._current_cells: dict[str, list[str]] = {}
        self._current_cell: str | None = None
        self._sequential_id: str | None = None

    def handle_starttag(
        self, tag: str, attributes: list[tuple[str, str | None]]
    ) -> None:
        attrs = _attributes(attributes)
        if tag == "input" and attrs.get("id") == "totalArchivos":
            try:
                self.total_items = int(attrs["value"])
            except (KeyError, ValueError) as exc:
                raise CatalogContractChanged("Catalog result total is not an integer") from exc

        classes = set(attrs.get("class", "").split())
        if tag == "tr" and "row100" in classes:
            if self._in_result_row:
                raise CatalogContractChanged("Nested catalog result rows are not supported")
            self._in_result_row = True
            self._current_cells = {}
            self._sequential_id = None
        elif tag == "td" and self._in_result_row and "data-th" in attrs:
            self._current_cell = attrs["data-th"]
            self._current_cells[self._current_cell] = []
        elif tag == "a" and self._in_result_row:
            element_id = attrs.get("id", "")
            if element_id.startswith("linkDescDir_"):
                self._sequential_id = element_id.removeprefix("linkDescDir_")
            elif not self._sequential_id and "detalleArchivo" in attrs.get("href", ""):
                query = parse_qs(urlparse(attrs["href"]).query)
                self._sequential_id = query.get("sec", [None])[0]

    def handle_data(self, data: str) -> None:
        if self._in_result_row and self._current_cell is not None and data.strip():
            self._current_cells[self._current_cell].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td" and self._in_result_row:
            self._current_cell = None
        elif tag == "tr" and self._in_result_row:
            self.items.append(self._build_item())
            self._in_result_row = False
            self._current_cell = None

    def _cell(self, name: str) -> str | None:
        parts = self._current_cells.get(name)
        if not parts:
            return None
        value = _normalized_text(parts)
        prefix = self._LABEL_PREFIXES.get(name)
        if prefix and value.startswith(prefix):
            value = value[len(prefix) :].strip()
        return value or None

    def _build_item(self) -> CatalogItem:
        filename = self._cell("Nombre")
        file_format = self._cell("Formato")
        if not filename or not file_format or not self._sequential_id:
            raise CatalogContractChanged(
                "Catalog row is missing filename, format, or sequential identifier"
            )

        year_text = self._cell("Fecha")
        size_text = self._cell("Tamaño\xa0(MB)")
        try:
            year = int(year_text) if year_text else None
            size_mb = float(size_text) if size_text else None
        except ValueError as exc:
            raise CatalogContractChanged("Catalog row contains invalid numeric metadata") from exc

        return CatalogItem(
            product=self.product,
            filename=filename,
            file_format=file_format,
            sequential_id=self._sequential_id,
            year=year,
            resolution=self._cell("Escala fotograma"),
            size_mb=size_mb,
        )


def parse_product_page(html: str, expected_product: DatasetProduct) -> ProductPage:
    """Parse dynamic discovery fields from one CNIG product page."""

    parser = _ProductPageParser()
    parser.feed(html)
    required = ("codAgr", "codSerie", "totalArchivos", "idsMenciones")
    missing = [name for name in required if not parser.hidden_fields.get(name)]
    if missing:
        raise CatalogContractChanged(f"Product page is missing fields: {', '.join(missing)}")
    if parser.hidden_fields["codSerie"] != expected_product.value:
        raise CatalogContractChanged(
            f"Expected {expected_product.value}, got {parser.hidden_fields['codSerie']}"
        )
    if "COG" not in parser.formats:
        raise CatalogContractChanged("Product page no longer advertises the COG format")

    try:
        total = int(parser.hidden_fields["totalArchivos"])
    except ValueError as exc:
        raise CatalogContractChanged("Product total is not an integer") from exc

    return ProductPage(
        catalog_group=parser.hidden_fields["codAgr"],
        catalog_series=parser.hidden_fields["codSerie"],
        advertised_total=total,
        attribution_ids=parser.hidden_fields["idsMenciones"],
        formats=tuple(parser.formats),
    )


def parse_catalog_page(html: str, product: DatasetProduct) -> CatalogPage:
    """Parse one HTML result page and reject ambiguous structural changes."""

    parser = _CatalogResultsParser(product)
    parser.feed(html)
    if parser.total_items is None:
        raise CatalogContractChanged("Catalog response is missing totalArchivos")
    if parser.total_items > 0 and not parser.items:
        raise CatalogContractChanged("Catalog response advertises items but has no parseable rows")
    return CatalogPage(total_items=parser.total_items, items=tuple(parser.items))
