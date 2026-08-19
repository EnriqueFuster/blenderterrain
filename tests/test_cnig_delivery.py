"""Security contract tests for CNIG temporary delivery authorization."""

from __future__ import annotations

import threading
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

from blender_terrain.errors import CatalogContractChanged, DownloadIntegrityError
from blender_terrain.io.html_download_parser import parse_presigned_download_url
from blender_terrain.models import CatalogItem, DatasetProduct
from blender_terrain.providers.cnig_delivery import (
    DELIVERY_HOST,
    normalized_resource_name,
    parse_storage_error_code,
    validate_presigned_download_url,
)
from blender_terrain.providers.cnig_portal import CNIGPortalClient, _NoRedirectHandler


NOW = datetime(2026, 8, 19, 12, 45, tzinfo=UTC)
ITEM = CatalogItem(
    product=DatasetProduct.MDT02,
    filename="MDT02-ETRS89-HU30-0722-1-COB2.TIF",
    file_format="COG",
    sequential_id="10324426",
)


def signed_url(**query_overrides: str) -> str:
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": "synthetic/test/credential",
        "X-Amz-Date": "20260819T124500Z",
        "X-Amz-Expires": "7199",
        "X-Amz-Signature": "synthetic-signature",
        "X-Amz-SignedHeaders": "host",
    }
    query.update(query_overrides)
    return (
        f"https://{DELIVERY_HOST}/modelos_digitales_terreno/MDT02/Huso30/"
        f"{ITEM.filename.lower()}?{urlencode(query)}"
    )


class DownloadHTMLParserTests(unittest.TestCase):
    def test_extracts_exactly_one_temporary_url(self) -> None:
        url = signed_url()
        html = f'<input type="hidden" id="urlPregsigned" value="{url}">'

        self.assertEqual(parse_presigned_download_url(html), url)

    def test_rejects_duplicate_temporary_urls(self) -> None:
        html = (
            '<input id="urlPregsigned" value="https://example.invalid/one">'
            '<input id="urlPregsigned" value="https://example.invalid/two">'
        )

        with self.assertRaises(CatalogContractChanged):
            parse_presigned_download_url(html)


class PresignedURLPolicyTests(unittest.TestCase):
    def test_accepts_current_cnig_delivery_shape(self) -> None:
        url = signed_url()

        self.assertEqual(validate_presigned_download_url(url, ITEM, now=NOW), url)

    def test_rejects_another_host(self) -> None:
        url = signed_url().replace(DELIVERY_HOST, "attacker.example")

        with self.assertRaises(DownloadIntegrityError):
            validate_presigned_download_url(url, ITEM, now=NOW)

    def test_rejects_another_object_name(self) -> None:
        url = signed_url().replace(ITEM.filename.lower(), "another-file.tif")

        with self.assertRaises(DownloadIntegrityError):
            validate_presigned_download_url(url, ITEM, now=NOW)

    def test_normalizes_only_observed_case_and_separator_differences(self) -> None:
        catalog_name = "MDS02-ETRS89-H30-PM-2-0722-1.TIF"
        delivery_name = "MDS02_ETRS89_H30_PM-2_0722-1.tif"

        self.assertEqual(
            normalized_resource_name(catalog_name),
            normalized_resource_name(delivery_name),
        )
        self.assertNotEqual(
            normalized_resource_name(catalog_name),
            normalized_resource_name("MDS02_ETRS89_H30_PM-2_0723-1.tif"),
        )

    def test_rejects_expired_authorization(self) -> None:
        url = signed_url(**{"X-Amz-Expires": "1"})
        later = datetime(2026, 8, 19, 12, 46, tzinfo=UTC)

        with self.assertRaises(DownloadIntegrityError):
            validate_presigned_download_url(url, ITEM, now=later)

    def test_rejects_unexpected_query_field(self) -> None:
        url = signed_url(unexpected="value")

        with self.assertRaises(DownloadIntegrityError):
            validate_presigned_download_url(url, ITEM, now=NOW)

    def test_extracts_only_safe_storage_error_code(self) -> None:
        body = b"<Error><Code>NoSuchKey</Code><RequestId>sensitive</RequestId></Error>"

        self.assertEqual(parse_storage_error_code(body), "NoSuchKey")
        self.assertIsNone(parse_storage_error_code(b"<html>not xml"))


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "/unexpected")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class RedirectPolicyTests(unittest.TestCase):
    def test_delivery_opener_has_no_cookie_processor(self) -> None:
        client = CNIGPortalClient()

        self.assertFalse(
            any(
                isinstance(handler, HTTPCookieProcessor)
                for handler in client._delivery_opener.handlers
            )
        )

    def test_delivery_opener_refuses_redirects(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{server.server_port}/source")
            with self.assertRaises(HTTPError) as context:
                build_opener(_NoRedirectHandler()).open(request, timeout=2)
            self.assertEqual(context.exception.code, 302)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
