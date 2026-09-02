"""Atomic job JSON and append-only progress persistence."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import JobFormatError
from .acquisition_job import AcquisitionJob
from .models import RESULT_SCHEMA_VERSION, DiscoveryJob, JobState, ProgressEvent

CANCELLATION_FILENAME = "cancel.request"


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


def write_acquisition_job(path: Path, job: AcquisitionJob) -> None:
    """Create a confirmed acquisition job without overwriting prior evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, job.to_dict(), overwrite=False)


def read_acquisition_job(path: Path) -> AcquisitionJob:
    """Read and validate a confirmed acquisition job."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise JobFormatError(f"Cannot read acquisition job JSON: {path}") from exc
    return AcquisitionJob.from_dict(payload)


def append_progress_event(path: Path, event: ProgressEvent) -> None:
    """Durably append one complete JSON Lines record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(event.to_dict(), separators=(",", ":"), sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_progress_events(path: Path, offset: int = 0) -> tuple[tuple[ProgressEvent, ...], int]:
    """Read only complete progress records appended after a byte offset."""

    if offset < 0:
        raise JobFormatError("Progress event offset must not be negative")
    if not path.is_file():
        return (), offset
    events: list[ProgressEvent] = []
    try:
        with path.open("rb") as stream:
            if offset > path.stat().st_size:
                offset = 0
            stream.seek(offset)
            next_offset = offset
            while line := stream.readline():
                if not line.endswith(b"\n"):
                    break
                next_offset = stream.tell()
                try:
                    payload = json.loads(line.decode("utf-8"))
                    events.append(ProgressEvent.from_dict(payload))
                except (UnicodeDecodeError, json.JSONDecodeError, JobFormatError):
                    continue
    except OSError as exc:
        raise JobFormatError(f"Cannot read progress events: {path}") from exc
    return tuple(events), next_offset


def write_result(path: Path, payload: dict[str, Any]) -> None:
    """Publish a terminal result atomically without replacing prior evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(path, payload, overwrite=False)


def finish_job_error(
    result_path: Path,
    emit: Callable[[JobState, float, str], None],
    state: JobState,
    message: str,
) -> JobState:
    """Persist and report one terminal worker error."""

    write_result(
        result_path,
        {"schema_version": RESULT_SCHEMA_VERSION, "state": state.value, "error": message},
    )
    emit(state, 1.0, message)
    return state


def request_cancellation(job_directory: Path) -> None:
    """Create an idempotent cancellation marker for a running worker."""

    job_directory.mkdir(parents=True, exist_ok=True)
    marker = job_directory / CANCELLATION_FILENAME
    try:
        marker.touch(exist_ok=True)
    except OSError as exc:
        raise JobFormatError("Cannot write the job cancellation request") from exc


def is_cancellation_requested(job_directory: Path) -> bool:
    """Return whether the Blender process requested cooperative cancellation."""

    return (job_directory / CANCELLATION_FILENAME).is_file()


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
