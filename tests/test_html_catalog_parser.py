"""Contract tests for reduced, sanitized CNIG HTML fixtures."""

from __future__ import annotations

import unittest
from pathlib import Path

from blender_terrain.errors import CatalogContractChanged
from blender_terrain.io.html_catalog_parser import parse_catalog_page, parse_product_page
from blender_terrain.models import DatasetProduct


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "portal_html"


def fixture(name: str) -> str:
    return (FIXTURE_ROOT / name).read_text(encoding="utf-8")


class ProductPageParserTests(unittest.TestCase):
    def test_parses_mdt02_dynamic_fields(self) -> None:
        page = parse_product_page(fixture("mdt02_product_page.html"), "MDT02")

        self.assertEqual(page.catalog_group, "MOMDT")
        self.assertEqual(page.advertised_total, 8308)
        self.assertEqual(page.formats, ("COG",))

    def test_parses_mds02_dynamic_fields(self) -> None:
        page = parse_product_page(fixture("mds02_product_page.html"), "MDS02")

        self.assertEqual(page.catalog_series, "MDS02")
        self.assertEqual(page.advertised_total, 8153)

    def test_rejects_missing_contract_field(self) -> None:
        html = fixture("mdt02_product_page.html").replace('id="codAgr"', 'id="changedCodAgr"')

        with self.assertRaisesRegex(CatalogContractChanged, "codAgr"):
            parse_product_page(html, "MDT02")

    def test_rejects_product_mismatch(self) -> None:
        with self.assertRaisesRegex(CatalogContractChanged, "Expected MDS02"):
            parse_product_page(fixture("mdt02_product_page.html"), "MDS02")

    def test_accepts_numeric_pnoa_catalog_series(self) -> None:
        html = """
            <input id="codAgr" value="FOTOR">
            <input id="codSerie" value="02211">
            <input id="totalArchivos" value="13764">
            <input id="idsMenciones" value="Ortofotos PNOA anuales">
            <select id="comboTipoArchSerie"><option value="COG">COG</option></select>
        """

        page = parse_product_page(html, "02211")

        self.assertEqual(page.catalog_group, "FOTOR")
        self.assertEqual(page.catalog_series, "02211")


class CatalogPageParserTests(unittest.TestCase):
    def test_parses_mdt02_rows_and_filters_native_variant(self) -> None:
        page = parse_catalog_page(fixture("mdt02_valencia_page_1.html"), DatasetProduct.MDT02)

        self.assertEqual(page.total_items, 2)
        self.assertEqual(len(page.items), 2)
        native = [item for item in page.items if item.is_native_projected_variant]
        self.assertEqual([item.filename for item in native], ["MDT02-ETRS89-HU30-0722-1-COB2.TIF"])
        self.assertEqual(native[0].sequential_id, "10324426")
        self.assertEqual(native[0].size_mb, 103.23)

    def test_parses_mds02_rows_and_filters_native_variant(self) -> None:
        page = parse_catalog_page(fixture("mds02_valencia_page_1.html"), DatasetProduct.MDS02)

        native = [item for item in page.items if item.is_native_projected_variant]
        self.assertEqual([item.filename for item in native], ["MDS02-ETRS89-H30-PM-2-0722-1.TIF"])
        self.assertEqual(native[0].sequential_id, "11610978")
        self.assertEqual(native[0].resolution, "2 m")

    def test_rejects_positive_total_without_rows(self) -> None:
        html = '<input type="hidden" id="totalArchivos" value="1">'

        with self.assertRaisesRegex(CatalogContractChanged, "no parseable rows"):
            parse_catalog_page(html, DatasetProduct.MDT02)

    def test_accepts_no_coverage_response(self) -> None:
        html = '<input type="hidden" id="totalArchivos" value="0">'

        page = parse_catalog_page(html, DatasetProduct.MDT02)

        self.assertEqual(page.total_items, 0)
        self.assertEqual(page.items, ())

    def test_preserves_month_and_multiple_pnoa_dates(self) -> None:
        html = """
            <input id="totalArchivos" value="1">
            <tr class="row100">
              <td data-th="Nombre">Archivo PNOA-MA-OF-ETRS89-HU30-H25-0722-1.TIF</td>
              <td data-th="Formato">Formato COG</td>
              <td data-th="Fecha">Fecha descarga 05/2024, 07/2024</td>
              <td data-th="Escala fotograma">Escala 0,25 m</td>
              <a id="linkDescDir_12570809"></a>
            </tr>
        """

        page = parse_catalog_page(html, DatasetProduct.PNOA_MA)

        self.assertEqual(page.items[0].date, "05/2024, 07/2024")
