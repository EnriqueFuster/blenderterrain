"""Launch and monitor BlenderTerrain background workers from Blender's UI."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy

from ..core import BBoxWGS84
from ..errors import JobFormatError, UserInputError
from ..jobs.models import DiscoveryJob, JobState
from ..jobs.storage import request_cancellation, write_discovery_job
from ..models import DatasetProduct

_POLL_INTERVAL_SECONDS = 0.25
_TERMINAL_STATES = {
    JobState.COMPLETE.value,
    JobState.CANCELLED.value,
    JobState.NO_COVERAGE.value,
    JobState.PROVIDER_CHANGED.value,
    JobState.NETWORK_ERROR.value,
    JobState.INVALID_DATA.value,
}
_extension_package = ""


@dataclass(slots=True)
class _ActiveJob:
    process: subprocess.Popen[bytes]
    directory: Path
    scene_name: str
    mode: str
    last_sequence: int = -1
    result_applied: bool = False


_active_job: _ActiveJob | None = None


def configure(extension_package: str) -> None:
    """Record the installed extension identifier used to find preferences."""

    global _extension_package
    _extension_package = extension_package


def start_discovery(context: bpy.types.Context) -> None:
    """Persist the current request and launch a factory-startup Blender worker."""

    properties = context.scene.blender_terrain_roi
    if not properties.is_valid:
        raise UserInputError("Validate the ROI before discovering sources")
    properties.discovery_ready = False
    _start_worker(context, properties, "discovery", "Starting background source discovery")


def start_delivery(context: bpy.types.Context) -> None:
    """Launch elevation and optional PNOA delivery in a background worker."""

    properties = context.scene.blender_terrain_roi
    if not properties.is_valid or not properties.discovery_ready:
        raise UserInputError("Discover the current sources before downloading data")
    properties.delivery_summary = ""
    _start_worker(context, properties, "delivery", "Starting background data download")


def _start_worker(context: bpy.types.Context, properties: Any, mode: str, message: str) -> None:
    global _active_job
    if _active_job is not None:
        raise UserInputError("Another BlenderTerrain job is already running")
    if not bpy.app.online_access:
        raise UserInputError("Blender online access is disabled in Preferences")
    cache_directory = _cache_directory(context)
    job_id = str(uuid4())
    job_directory = cache_directory / "jobs" / job_id
    job_path = job_directory / "job.json"
    write_discovery_job(job_path, _job_from_properties(job_id, properties))

    worker_entry = Path(__file__).resolve().parents[1] / "jobs" / "worker_entry.py"
    command = [
        bpy.app.binary_path,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(worker_entry),
        "--",
        str(job_path),
    ]
    if mode == "delivery":
        command.append("delivery")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path = job_directory / "worker.log"
    try:
        with log_path.open("wb") as log_stream:
            process = subprocess.Popen(
                command,
                cwd=worker_entry.parents[2],
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
    except OSError as exc:
        raise UserInputError(f"Cannot start the Blender background worker: {exc}") from exc

    _active_job = _ActiveJob(process, job_directory, context.scene.name, mode)
    properties.job_active = True
    properties.job_state = JobState.VALIDATING.value
    properties.job_progress = 0.0
    properties.job_message = message
    if mode == "discovery":
        properties.discovery_summary = ""
    if not bpy.app.timers.is_registered(_poll_active_job):
        bpy.app.timers.register(_poll_active_job, first_interval=_POLL_INTERVAL_SECONDS)


def cancel_discovery() -> None:
    """Ask the current worker to stop at its next safe checkpoint."""

    if _active_job is None:
        raise UserInputError("There is no active job to cancel")
    request_cancellation(_active_job.directory)
    properties = _scene_properties(_active_job.scene_name)
    if properties is not None:
        properties.job_message = "Cancellation requested; waiting for the current request"


def shutdown() -> None:
    """Stop monitoring and terminate only the worker owned by this extension."""

    global _active_job
    if bpy.app.timers.is_registered(_poll_active_job):
        bpy.app.timers.unregister(_poll_active_job)
    if _active_job is not None and _active_job.process.poll() is None:
        request_cancellation(_active_job.directory)
        _active_job.process.terminate()
    _active_job = None


def has_active_job() -> bool:
    """Return whether this Blender process owns a running discovery job."""

    return _active_job is not None


def timer_is_registered() -> bool:
    """Expose timer ownership for Blender registration smoke tests."""

    return bpy.app.timers.is_registered(_poll_active_job)


def _poll_active_job() -> float | None:
    global _active_job
    active = _active_job
    if active is None:
        return None
    properties = _scene_properties(active.scene_name)
    if properties is None:
        _stop_orphaned_job(active)
        _active_job = None
        return None

    _apply_new_events(active, properties)
    result_path = active.directory / "result.json"
    if result_path.is_file() and not active.result_applied:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _POLL_INTERVAL_SECONDS
        _apply_result(active, properties, result)
        active.result_applied = True

    return_code = active.process.poll()
    if active.result_applied and return_code is not None:
        _active_job = None
        return None
    if return_code is not None:
        properties.job_active = False
        properties.job_state = JobState.INVALID_DATA.value
        properties.job_progress = 1.0
        properties.job_message = f"Background worker stopped without a result (exit {return_code})"
        _active_job = None
        return None
    return _POLL_INTERVAL_SECONDS


def _apply_new_events(active: _ActiveJob, properties: Any) -> None:
    events_path = active.directory / "events.jsonl"
    if not events_path.is_file():
        return
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        try:
            event = json.loads(line)
            sequence = int(event["sequence"])
            state = str(event["state"])
            progress = float(event["progress"])
            message = str(event["message"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if sequence <= active.last_sequence:
            continue
        active.last_sequence = sequence
        properties.job_state = state
        properties.job_progress = max(0.0, min(1.0, progress))
        properties.job_message = message


def _apply_result(active: _ActiveJob, properties: Any, result: dict[str, Any]) -> None:
    state = str(result.get("state", JobState.INVALID_DATA.value))
    if state not in _TERMINAL_STATES:
        state = JobState.INVALID_DATA.value
    properties.job_active = False
    properties.job_state = state
    properties.job_progress = 1.0
    if state == JobState.COMPLETE.value:
        if active.mode == "delivery":
            elevation_count = len(result.get("elevation_paths", []))
            imagery_count = len(result.get("imagery_paths", []))
            terrain_count = len(result.get("processed_elevation", []))
            properties.delivery_ready = True
            properties.delivery_summary = (
                f"Prepared {elevation_count} elevation, {imagery_count} imagery and "
                f"{terrain_count} terrain tile(s)"
            )
            properties.job_message = "Data download completed"
            return
        count = len(result.get("items", []))
        estimated_mb = result.get("estimated_download_mb")
        properties.discovered_file_count = count
        properties.estimated_download_mb = float(estimated_mb or 0.0)
        size_text = (
            f", approximately {properties.estimated_download_mb:.1f} MB"
            if estimated_mb is not None
            else ", size unavailable"
        )
        properties.discovery_summary = f"{count} elevation source file(s){size_text}"
        properties.discovery_ready = True
        properties.job_message = "Source discovery completed"
    else:
        if active.mode == "discovery":
            properties.discovery_summary = ""
            properties.discovery_ready = False
        else:
            properties.delivery_ready = False
            properties.delivery_summary = ""
        properties.job_message = str(result.get("error", f"Discovery ended as {state}"))


def _cache_directory(context: bpy.types.Context) -> Path:
    if not _extension_package:
        raise UserInputError("The extension has not initialized its preferences")
    addon = context.preferences.addons.get(_extension_package)
    if addon is None or not addon.preferences.cache_directory.strip():
        raise UserInputError("Choose a cache directory in the BlenderTerrain preferences")
    path = Path(bpy.path.abspath(addon.preferences.cache_directory)).expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserInputError(f"Cannot create the cache directory: {path}") from exc
    return path


def _job_from_properties(job_id: str, properties: Any) -> DiscoveryJob:
    try:
        return DiscoveryJob(
            job_id=job_id,
            bounds=BBoxWGS84(
                properties.west,
                properties.south,
                properties.east,
                properties.north,
            ),
            product=DatasetProduct(properties.product),
            elevation_resolution_metres=(
                None
                if properties.elevation_resolution == "AUTO"
                else float(properties.elevation_resolution)
            ),
            use_imagery=properties.use_imagery,
            imagery_gsd_metres=(
                None if properties.imagery_gsd == "AUTO" else float(properties.imagery_gsd)
            ),
        )
    except (JobFormatError, ValueError) as exc:
        raise UserInputError(f"Cannot create the discovery job: {exc}") from exc


def _scene_properties(scene_name: str) -> Any | None:
    scene = bpy.data.scenes.get(scene_name)
    return None if scene is None else scene.blender_terrain_roi


def _stop_orphaned_job(active: _ActiveJob) -> None:
    if active.process.poll() is None:
        request_cancellation(active.directory)
        active.process.terminate()
