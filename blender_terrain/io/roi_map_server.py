"""Ephemeral loopback server used by the browser-based ROI selector."""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..core.roi import BBoxWGS84, RegionOfInterest
from ..errors import UserInputError

MAX_MAP_RESULT_BYTES = 2 * 1024 * 1024
_ASSET_DIRECTORY = Path(__file__).parents[1] / "assets"
_MAP_LAYERS = (
    {
        "id": "PNOA",
        "name": "PNOA Orthophoto (Aerial)",
        "url": (
            "https://www.ign.es/wmts/pnoa-ma?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            "&LAYER=OI.OrthoimageCoverage&STYLE=default&FORMAT=image/jpeg"
            "&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        ),
        "attribution": "© Instituto Geográfico Nacional",
        "attribution_url": "https://www.ign.es/",
    },
    {
        "id": "RELIEF",
        "name": "IGN Physical Relief",
        "url": (
            "https://servicios.idee.es/wmts/mdt?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            "&LAYER=Relieve&STYLE=Default&FORMAT=image/jpeg"
            "&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        ),
        "attribution": "© Instituto Geográfico Nacional",
        "attribution_url": "https://www.ign.es/",
    },
    {
        "id": "IGN_BASE",
        "name": "IGN Topographic Map",
        "url": (
            "https://www.ign.es/wmts/ign-base?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0"
            "&LAYER=IGNBaseTodo&STYLE=default&FORMAT=image/jpeg"
            "&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
        ),
        "attribution": "© Instituto Geográfico Nacional",
        "attribution_url": "https://www.ign.es/",
    },
    {
        "id": "OSM",
        "name": "OpenStreetMap Streets",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors",
        "attribution_url": "https://www.openstreetmap.org/copyright",
    },
)


@dataclass(slots=True)
class ROIMapSession:
    """One authenticated, single-use browser selection session."""

    mode: str
    initial_bounds: BBoxWGS84
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    result: RegionOfInterest | None = None
    error: str = ""
    cancelled: bool = False
    finished: threading.Event = field(default_factory=threading.Event)
    _server: ThreadingHTTPServer | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in {"RECTANGLE", "POLYGON"}:
            raise UserInputError("Map selection mode must be RECTANGLE or POLYGON")

    def start(self) -> str:
        """Start listening on a random loopback port and return the authenticated URL."""

        if self._server is not None:
            raise RuntimeError("ROI map session has already started")
        handler = _handler_for(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="BlenderTerrainROIMap",
            daemon=True,
        )
        self._thread.start()
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/?token={self.token}"

    def close(self) -> None:
        """Stop the local server without waiting indefinitely for its thread."""

        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


def _handler_for(session: ROIMapSession) -> type[BaseHTTPRequestHandler]:
    class ROIMapHandler(BaseHTTPRequestHandler):
        server_version = "BlenderTerrainROIMap/1"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._serve_asset("roi_map.html", "text/html; charset=utf-8")
            elif parsed.path == "/app.js":
                self._serve_asset("roi_map.js", "text/javascript; charset=utf-8")
            elif parsed.path == "/config" and self._authorized(parsed.query):
                self._send_json(
                    {
                        "mode": session.mode,
                        "bounds": {
                            "west": session.initial_bounds.west,
                            "south": session.initial_bounds.south,
                            "east": session.initial_bounds.east,
                            "north": session.initial_bounds.north,
                        },
                        "default_layer": "PNOA",
                        "layers": _MAP_LAYERS,
                    }
                )
            else:
                self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if not self._authorized(parsed.query):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if parsed.path == "/cancel":
                session.cancelled = True
                session.finished.set()
                self._send_json({"ok": True})
                return
            if parsed.path != "/result":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_MAP_RESULT_BYTES:
                    raise UserInputError("Map result has an invalid size")
                payload = json.loads(self.rfile.read(length))
                session.result = RegionOfInterest.from_geojson_geometry(payload)
            except (json.JSONDecodeError, TypeError, ValueError, UserInputError) as error:
                session.error = str(error)
                self._send_json({"ok": False, "error": session.error}, HTTPStatus.BAD_REQUEST)
                return
            session.finished.set()
            self._send_json({"ok": True})

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _authorized(self, query: str) -> bool:
            return parse_qs(query).get("token") == [session.token]

        def _serve_asset(self, filename: str, content_type: str) -> None:
            try:
                content = (_ASSET_DIRECTORY / filename).read_bytes()
            except OSError:
                self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src https://tile.openstreetmap.org https://www.ign.es "
                "https://servicios.idee.es data:; "
                "style-src 'self' 'unsafe-inline'",
            )
            self.end_headers()
            self.wfile.write(content)

        def _send_json(
            self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK
        ) -> None:
            content = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return ROIMapHandler
