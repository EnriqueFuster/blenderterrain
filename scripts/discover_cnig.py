"""Run the opt-in, read-only CNIG Phase 0 discovery experiment."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from blender_terrain.models import DatasetProduct
from blender_terrain.providers.cnig_portal import BBoxWGS84, CNIGPortalClient


VALENCIA_TEST_BBOX = BBoxWGS84(west=-0.39, south=39.46, east=-0.37, north=39.48)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--product",
        choices=[product.value for product in DatasetProduct],
        required=True,
    )
    parser.add_argument(
        "--online",
        action="store_true",
        help="Explicitly allow read-only requests to the official CNIG portal.",
    )
    args = parser.parse_args()
    if not args.online:
        parser.error("online access is disabled; pass --online to run discovery")

    product = DatasetProduct(args.product)
    page = CNIGPortalClient().discover(product, VALENCIA_TEST_BBOX)
    projected_items = [item for item in page.items if item.is_native_projected_variant]
    output = {
        "product": product.value,
        "query_bbox_wgs84": asdict(VALENCIA_TEST_BBOX),
        "reported_total": page.total_items,
        "native_projected_items": [asdict(item) for item in projected_items],
        "excluded_items": [
            asdict(item) for item in page.items if not item.is_native_projected_variant
        ],
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
