"""Atomic job JSON and append-only progress persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..errors import JobFormatError
from .models import DiscoveryJob, ProgressEvent


def write_discovery_job(path: Path, job: DiscoveryJob) -> None:
    """Create a job file atomically without overwriting an existing job."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, job.to_dict(), overwrite=False)


def read_discovery_job(path: Path) -> DiscoveryJob:
    """Read and validate a discovery job file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobFormatError(f"Cannot read job JSON: {path}") from exc
    return DiscoveryJob.from_dict(payload)


def append_progress_event(path: Path, event: ProgressEvent) -> None:
    """Durably append one complete JSON Lines record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def write_result(path: Path, payload: dict[str, Any]) -> None:
    """Publish a terminal result atomically without replacing prior evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, payload, overwrite=False)


def _write_json_atomic(path: Path, payload: dict[str, Any], overwrite: bool) -> None:
    temporary = path.with_name(path.name + ".part")
    if temporary.exists() or (path.exists() and not overwrite):
        raise JobFormatError(f"Refusing to overwrite job artifact: {path.name}")
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
