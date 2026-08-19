"""Contract and transport tests for direct CNIG downloads."""

from __future__ import annotations

import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from blender_terrain.errors import CatalogContractChanged, DownloadAuthorizationRequired
from blender_terrain.io.atomic import normalized_server_filename
from blender_terrain.providers.cnig_portal import CNIGPortalClient, _NoRedirectHandler


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "portal_json"


class DownloadInitializationTests(unittest.TestCase):
    def test_accepts_matching_direct_download_authorization(self) -> None:
        body = (FIXTURE_ROOT / "download_init_mdt02.json").read_text(encoding="utf-8")

        sequence = CNIGPortalClient._parse_download_initialization(body, "11275366")

        self.assertEqual(sequence, "11275366")

    def test_rejects_different_authorized_resource(self) -> None:
        body = '{"muestraLic":"NO","secuencialDescDir":"another-resource"}'

        with self.assertRaises(CatalogContractChanged):
            CNIGPortalClient._parse_download_initialization(body, "11275366")

    def test_reports_interactive_license_requirement(self) -> None:
        body = '{"muestraLic":"SI","secuencialDescDir":"11275366"}'

        with self.assertRaises(DownloadAuthorizationRequired):
            CNIGPortalClient._parse_download_initialization(body, "11275366")

    def test_normalizes_only_observed_filename_differences(self) -> None:
        catalog_name = "MDS02-ETRS89-H30-PM-2-0722-1.TIF"
        delivered_name = "MDS02_ETRS89_H30_PM-2_0722-1.tif"

        self.assertEqual(
            normalized_server_filename(catalog_name),
            normalized_server_filename(delivered_name),
        )
        self.assertNotEqual(
            normalized_server_filename(catalog_name),
            normalized_server_filename("MDS02_ETRS89_H30_PM-2_0723-1.tif"),
        )


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "/unexpected")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


class DirectDownloadTransportTests(unittest.TestCase):
    def test_download_opener_shares_only_the_cnig_cookie_jar(self) -> None:
        client = CNIGPortalClient()
        cookie_handlers = [
            handler
            for handler in client._download_opener.handlers
            if isinstance(handler, HTTPCookieProcessor)
        ]

        self.assertEqual(len(cookie_handlers), 1)
        self.assertIs(cookie_handlers[0].cookiejar, client._cookie_jar)

    def test_download_opener_refuses_redirects(self) -> None:
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
