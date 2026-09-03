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
    from blender_terrain.jobs.cnig import (
        run_cnig_availability_job,
        run_cnig_discovery_job,
    )
    from blender_terrain.jobs.local import (
        run_local_delivery_job,
        run_local_discovery_job,
    )
    from blender_terrain.jobs.worker import run_confirmed_acquisition_job

    job_path = Path(arguments[0]).resolve()
    if len(arguments) == 2 and arguments[1] == "acquisition":
        run_confirmed_acquisition_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "cnig_availability":
        run_cnig_availability_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "local_discovery":
        run_local_discovery_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "local_delivery":
        run_local_delivery_job(job_path)
    elif len(arguments) == 2 and arguments[1] == "cnig_discovery":
        run_cnig_discovery_job(job_path)
    else:
        raise RuntimeError(f"Unsupported worker mode: {arguments[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
