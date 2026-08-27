from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

import pytest

from blender_terrain.core.roi import BBoxWGS84
from blender_terrain.io.roi_map_server import ROIMapSession


def test_map_server_returns_config_and_accepts_one_polygon() -> None:
    session = ROIMapSession("POLYGON", BBoxWGS84(-4, 40, -3, 41))
    url = session.start()
    parsed = urlparse(url)
    token = parse_qs(parsed.query)["token"][0]
    origin = f"http://{parsed.netloc}"
    try:
        with urlopen(f"{origin}/config?token={token}", timeout=2) as response:
            config = json.load(response)
        assert config["mode"] == "POLYGON"
        assert config["bounds"]["west"] == -4
        assert config["default_layer"] == "PNOA"
        assert [layer["id"] for layer in config["layers"]] == [
            "PNOA",
            "RELIEF",
            "IGN_BASE",
            "OSM",
        ]

        geometry = {
            "type": "Polygon",
            "coordinates": [[[-4, 40], [-3, 40], [-3, 41], [-4, 41], [-4, 40]]],
        }
        request = Request(
            f"{origin}/result?token={token}",
            data=json.dumps(geometry).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            assert json.load(response) == {"ok": True}

        assert session.finished.wait(1)
        assert session.result is not None
        assert session.result.bounds == BBoxWGS84(-4, 40, -3, 41)
    finally:
        session.close()


def test_map_server_rejects_an_invalid_token() -> None:
    session = ROIMapSession("RECTANGLE", BBoxWGS84(-4, 40, -3, 41))
    url = session.start()
    origin = f"http://{urlparse(url).netloc}"
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f"{origin}/config?token=wrong", timeout=2)
        assert error.value.code == 404
    finally:
        session.close()
