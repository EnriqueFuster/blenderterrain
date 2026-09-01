"""Blender-background entry point for a persisted discovery job."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    """Bootstrap the portable package when Blender executes this file directly."""

    arguments = sys.argv[sys.argv.index("--") + 1 :]
    if len(arguments) not in {1, 2}:
        raise RuntimeError("Expected a job.json path and optional worker mode after --")
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from blender_terrain.jobs.worker import (
        run_availability_job,
        run_confirmed_acquisition_job,
        run_delivery_job,
        run_discovery_job,
    )

    job_path = Path(arguments[0]).resolve()
    if len(arguments) == 2 and arguments[1] == "delivery":
        run_delivery_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "acquisition":
        run_confirmed_acquisition_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "availability":
        run_availability_job(job_path)
    elif len(arguments) == 1:
        run_discovery_job(job_path)
    else:
        raise RuntimeError(f"Unsupported worker mode: {arguments[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
