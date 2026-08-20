"""Run opt-in CNIG catalog discovery and a controlled sample download."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

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
        help="Explicitly allow requests to the official CNIG portal.",
    )
    parser.add_argument(
        "--download-one",
        action="store_true",
        help="Download the single native projected item into --download-directory.",
    )
    parser.add_argument(
        "--download-directory",
        type=Path,
        help="Directory for the opt-in sample download.",
    )
    parser.add_argument(
        "--sequential-id",
        help="Download the discovered item with this exact CNIG sequential identifier.",
    )
    args = parser.parse_args()
    if not args.online:
        parser.error("online access is disabled; pass --online to run discovery")

    product = DatasetProduct(args.product)
    client = CNIGPortalClient()
    page = client.discover(product, VALENCIA_TEST_BBOX)
    projected_items = [item for item in page.items if item.is_native_projected_variant]
    if args.download_one or args.sequential_id:
        if args.download_directory is None:
            parser.error("downloading requires --download-directory")
        if args.sequential_id:
            selected_items = [
                item for item in projected_items if item.sequential_id == args.sequential_id
            ]
            if len(selected_items) != 1:
                parser.error("sequential identifier did not select exactly one discovered item")
        else:
            selected_items = projected_items
            if len(selected_items) != 1:
                parser.error("test query did not return exactly one native projected item")
        downloaded_path = client.download_item(selected_items[0], args.download_directory)
    else:
        downloaded_path = None
    output = {
        "product": product.value,
        "query_bbox_wgs84": asdict(VALENCIA_TEST_BBOX),
        "reported_total": page.total_items,
        "native_projected_items": [asdict(item) for item in projected_items],
        "excluded_items": [
            asdict(item) for item in page.items if not item.is_native_projected_variant
        ],
        "downloaded_path": str(downloaded_path) if downloaded_path else None,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
