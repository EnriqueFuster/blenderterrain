"""Run an opt-in PNOA WMS capabilities and control-image check."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from blender_terrain.models import ProjectedBounds
from blender_terrain.providers.pnoa_wms import PNOAWMSClient

CONTROL_BOUNDS = ProjectedBounds(
    west=713500.0,
    south=4374500.0,
    east=713628.0,
    north=4374628.0,
    epsg=25830,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Allow official WMS requests.")
    parser.add_argument("--download-directory", type=Path)
    args = parser.parse_args()
    if not args.online:
        parser.error("online access is disabled; pass --online to run the check")

    client = PNOAWMSClient()
    capabilities = client.capabilities()
    image_path = None
    if args.download_directory is not None:
        image_path = client.download_png(
            CONTROL_BOUNDS,
            width=512,
            height=512,
            cache_directory=args.download_directory,
            filename="pnoa-wms-epsg25830-control.png",
        )
    print(
        json.dumps(
            {
                "capabilities": asdict(capabilities),
                "control_bounds": asdict(CONTROL_BOUNDS),
                "image_path": str(image_path) if image_path else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
