"""Blender-background entry point for a persisted discovery job."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Bootstrap the portable package when Blender executes this file directly."""

    arguments = sys.argv[sys.argv.index("--") + 1 :]
    if len(arguments) != 1:
        raise RuntimeError("Expected one job.json path after --")
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from blender_terrain.jobs.worker import run_discovery_job

    run_discovery_job(Path(arguments[0]).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
