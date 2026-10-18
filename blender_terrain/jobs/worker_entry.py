"""Blender-background entry point for a persisted discovery job."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path


def main() -> int:
    """Bootstrap the portable package when Blender executes this file directly."""

    arguments = sys.argv[sys.argv.index("--") + 1 :]
    if len(arguments) not in {1, 2}:
        raise RuntimeError("Expected a job.json path and optional worker mode after --")
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from blender_terrain.jobs.local import (
        run_local_delivery_job,
        run_local_discovery_job,
    )
    from blender_terrain.jobs.worker import run_confirmed_acquisition_job
    from blender_terrain.providers.registry import provider_job_runners

    job_path = Path(arguments[0]).resolve()
    runners: dict[str, Callable[[Path], object]] = {
        "acquisition": run_confirmed_acquisition_job,
        "local_discovery": run_local_discovery_job,
        "local_delivery": run_local_delivery_job,
        **provider_job_runners(),
    }
    mode = arguments[1] if len(arguments) == 2 else ""
    runner = runners.get(mode)
    if runner is None:
        raise RuntimeError(f"Unsupported worker mode: {arguments[1]}")
    runner(job_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
