"""Probe French IGN WMS products with bounded read-only requests."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

from blender_terrain.catalog import WMSContract, load_bundled_catalog
from blender_terrain.io.wms_capabilities import parse_wms_capabilities

USER_AGENT = "BlenderTerrain/0.5-source-probe"
CAPABILITIES_LIMIT = 16 * 1024 * 1024
CONTROL_BBOX = (651000.0, 6861000.0, 651064.0, 6861064.0)
CONTROL_SIZE = 64


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    report = probe_products()
    destination = arguments.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(destination)


def probe_products() -> dict[str, Any]:
    """Return validated service metadata and small sample summaries."""

    products = tuple(
        product
        for product in load_bundled_catalog().products
        if product.provider_id == "ign_france"
    )
    endpoint = products[0].endpoint
    capabilities_query = urlencode(
        {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetCapabilities"}
    )
    capabilities_body, capabilities_type = _request(
        f"{endpoint}?{capabilities_query}",
        CAPABILITIES_LIMIT,
    )
    if capabilities_type not in {"application/xml", "text/xml"}:
        raise RuntimeError("French WMS capabilities did not return XML")

    results: dict[str, Any] = {}
    for product in products:
        contract = product.wms
        if contract is None:
            raise RuntimeError(f"{product.id} has no executable WMS contract")
        advertised = parse_wms_capabilities(
            capabilities_body, contract.layer, contract.format
        )
        if f"EPSG:{contract.crs_epsg}" not in advertised.crs:
            raise RuntimeError(f"{product.id} no longer advertises EPSG:{contract.crs_epsg}")
        body, content_type = _request(_map_url(product.endpoint, contract), 2_000_000)
        sample = (
            _bil_summary(body, content_type)
            if contract.sample_dtype is not None
            else _png_summary(body, content_type)
        )
        results[product.id] = {
            "layer": contract.layer,
            "format": contract.format,
            "advertised_maximum": [advertised.max_width, advertised.max_height],
            "configured_maximum": contract.maximum_dimension,
            "sample": sample,
        }
    return {
        "endpoint": endpoint,
        "bbox_epsg2154": list(CONTROL_BBOX),
        "width": CONTROL_SIZE,
        "height": CONTROL_SIZE,
        "capabilities_sha256": hashlib.sha256(capabilities_body).hexdigest(),
        "products": results,
    }


def _map_url(endpoint: str, contract: WMSContract) -> str:
    query = urlencode(
        {
            "SERVICE": "WMS",
            "VERSION": contract.version,
            "REQUEST": "GetMap",
            "LAYERS": contract.layer,
            "STYLES": contract.style,
            "CRS": f"EPSG:{contract.crs_epsg}",
            "BBOX": ",".join(str(value) for value in CONTROL_BBOX),
            "WIDTH": str(CONTROL_SIZE),
            "HEIGHT": str(CONTROL_SIZE),
            "FORMAT": contract.format,
            "TRANSPARENT": "FALSE",
        }
    )
    return f"{endpoint}?{query}"


def _request(url: str, maximum_bytes: int) -> tuple[bytes, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        body = response.read(maximum_bytes + 1)
        content_type = response.headers.get_content_type().lower()
    if len(body) > maximum_bytes:
        raise RuntimeError("French WMS response exceeds the probe limit")
    return body, content_type


def _bil_summary(body: bytes, content_type: str) -> dict[str, Any]:
    expected_bytes = CONTROL_SIZE * CONTROL_SIZE * 4
    if content_type != "image/x-bil" or len(body) != expected_bytes:
        raise RuntimeError("French elevation response is not the expected BIL32 window")
    values = np.frombuffer(body, dtype="<f4")
    valid = values[np.isfinite(values) & (values != -99999.0)]
    if not valid.size:
        raise RuntimeError("French elevation control window contains no valid samples")
    return {
        "content_type": content_type,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
        "minimum": float(valid.min()),
        "maximum": float(valid.max()),
    }


def _png_summary(body: bytes, content_type: str) -> dict[str, Any]:
    if (
        content_type != "image/png"
        or not body.startswith(b"\x89PNG\r\n\x1a\n")
        or b"IEND" not in body[-16:]
    ):
        raise RuntimeError("French imagery response is not a complete PNG")
    return {
        "content_type": content_type,
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }


if __name__ == "__main__":
    main()
