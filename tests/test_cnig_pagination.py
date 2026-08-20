from __future__ import annotations

import unittest
from pathlib import Path
from urllib.parse import parse_qs

from blender_terrain.core import BBoxWGS84
from blender_terrain.errors import CatalogContractChanged
from blender_terrain.models import DatasetProduct
from blender_terrain.providers.cnig_portal import CNIGPortalClient

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "portal_html"


def catalog_html(total: int, rows: tuple[tuple[str, str], ...]) -> str:
    rendered_rows = "".join(
        f"""
        <tr class="row100">
          <td data-th="Nombre">Archivo {filename}</td>
          <td data-th="Formato">Formato COG</td>
          <a id="linkDescDir_{sequential_id}"></a>
        </tr>
        """
        for filename, sequential_id in rows
    )
    return f'<input id="totalArchivos" value="{total}">{rendered_rows}'


class FakePaginatedClient(CNIGPortalClient):
    def __init__(self, pages: dict[int, str]) -> None:
        super().__init__()
        self.pages = pages
        self.requested_pages: list[int] = []

    def _request(
        self,
        url: str,
        data: bytes | None = None,
        referer: str | None = None,
        maximum_bytes: int | None = None,
    ) -> str:
        if not url.endswith("archivosSerie"):
            return (FIXTURE_ROOT / "mdt02_product_page.html").read_text(encoding="utf-8")
        assert data is not None
        page_number = int(parse_qs(data.decode("utf-8"))["numPagina"][0])
        self.requested_pages.append(page_number)
        return self.pages[page_number]


class CNIGPaginationTests(unittest.TestCase):
    def test_collects_all_pages_in_provider_order(self) -> None:
        client = FakePaginatedClient(
            {
                1: catalog_html(
                    3,
                    (
                        ("MDT02-ETRS89-HU30-A.TIF", "1"),
                        ("MDT02-WGS84-A.TIF", "2"),
                    ),
                ),
                2: catalog_html(3, (("MDT02-ETRS89-HU30-B.TIF", "3"),)),
            }
        )

        page = client.discover_all(
            DatasetProduct.MDT02, BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
        )

        self.assertEqual(client.requested_pages, [1, 2])
        self.assertEqual([entry.sequential_id for entry in page.items], ["1", "2", "3"])

    def test_rejects_pagination_without_new_items(self) -> None:
        repeated = catalog_html(2, (("MDT02-ETRS89-HU30-A.TIF", "1"),))
        client = FakePaginatedClient({1: repeated, 2: repeated})

        with self.assertRaisesRegex(CatalogContractChanged, "no progress"):
            client.discover_all(
                DatasetProduct.MDT02, BBoxWGS84(-0.39, 39.46, -0.37, 39.48)
            )


if __name__ == "__main__":
    unittest.main()
