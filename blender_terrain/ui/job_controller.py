"""Launch and monitor BlenderTerrain background workers from Blender's UI."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy

from ..core import RegionOfInterest
from ..errors import JobFormatError, UserInputError
from ..jobs.models import DiscoveryJob, JobState
from ..jobs.storage import read_progress_events, request_cancellation, write_discovery_job
from ..models import DatasetProduct

_POLL_INTERVAL_SECONDS = 0.25
_TERMINAL_STATES = {
    JobState.COMPLETE.value,
    JobState.COMPLETE_WITH_WARNINGS.value,
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
    event_offset: int = 0
    started_monotonic: float = 0.0


_active_job: _ActiveJob | None = None


def configure(extension_package: str) -> None:
    """Record the installed extension identifier used to find preferences."""

    global _extension_package
    _extension_package = extension_package


def recover_interrupted_jobs() -> int:
    """Unlock scenes that persisted a worker no longer owned by this process."""

    if _active_job is not None or not hasattr(bpy.data, "scenes"):
        return 0
    recovered = 0
    for scene in bpy.data.scenes:
        properties = scene.blender_terrain_roi
        if not properties.job_active:
            continue
        mode = properties.active_job_mode
        properties.job_active = False
        properties.active_job_mode = ""
        properties.job_state = JobState.INVALID_DATA.value
        properties.job_progress = 1.0
        properties.job_message = "Previous background task was interrupted; retry when ready"
        if mode == "discovery":
            properties.discovery_ready = False
            properties.discovery_summary = ""
        else:
            properties.delivery_ready = False
            properties.delivery_summary = ""
            properties.delivery_result_path = ""
            properties.imagery_available = False
            properties.imagery_size_mib = 0.0
        recovered += 1
    return recovered


def schedule_interrupted_job_recovery() -> None:
    """Recover now or defer until Blender releases registration-time data access."""

    if hasattr(bpy.data, "scenes"):
        recover_interrupted_jobs()
    elif not bpy.app.timers.is_registered(_recover_interrupted_jobs_on_timer):
        bpy.app.timers.register(_recover_interrupted_jobs_on_timer, first_interval=0.0)


def _recover_interrupted_jobs_on_timer() -> None:
    recover_interrupted_jobs()


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
    task_id = str(uuid4())
    if not properties.import_id:
        properties.import_id = str(uuid4())
    job_directory = cache_directory / "jobs" / task_id
    job_path = job_directory / "job.json"
    write_discovery_job(
        job_path, _job_from_properties(task_id, properties.import_id, properties)
    )

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

    _active_job = _ActiveJob(
        process,
        job_directory,
        context.scene.name,
        mode,
        started_monotonic=time.monotonic(),
    )
    properties.job_active = True
    properties.active_job_mode = mode
    properties.job_state = JobState.VALIDATING.value
    properties.job_progress = 0.0
    properties.job_message = message
    properties.job_event_history = json.dumps([message])
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
    if bpy.app.timers.is_registered(_recover_interrupted_jobs_on_timer):
        bpy.app.timers.unregister(_recover_interrupted_jobs_on_timer)
    if bpy.app.timers.is_registered(_poll_active_job):
        bpy.app.timers.unregister(_poll_active_job)
    if _active_job is not None:
        properties = _scene_properties(_active_job.scene_name)
        if _active_job.process.poll() is None:
            request_cancellation(_active_job.directory)
            _active_job.process.terminate()
        if properties is not None:
            properties.job_active = False
            properties.active_job_mode = ""
            properties.job_state = JobState.CANCELLED.value
            properties.job_progress = 1.0
            properties.job_message = "Background task stopped because the extension closed"
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

    changed = _apply_new_events(active, properties)
    result_path = active.directory / "result.json"
    if result_path.is_file() and not active.result_applied:
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _POLL_INTERVAL_SECONDS
        _apply_result(active, properties, result)
        active.result_applied = True
        changed = True

    if changed:
        _redraw_extension_ui()

    return_code = active.process.poll()
    if active.result_applied and return_code is not None:
        _active_job = None
        return None
    if return_code is not None:
        properties.job_active = False
        properties.active_job_mode = ""
        properties.job_state = JobState.INVALID_DATA.value
        properties.job_progress = 1.0
        properties.job_message = f"Background worker stopped without a result (exit {return_code})"
        _active_job = None
        return None
    return _POLL_INTERVAL_SECONDS


def _apply_new_events(active: _ActiveJob, properties: Any) -> bool:
    events_path = active.directory / "events.jsonl"
    try:
        events, active.event_offset = read_progress_events(
            events_path, active.event_offset
        )
    except JobFormatError:
        return False
    changed = False
    history = _event_history(properties.job_event_history)
    for event in events:
        if event.sequence <= active.last_sequence:
            continue
        active.last_sequence = event.sequence
        properties.job_state = event.state.value
        properties.job_progress = event.progress
        properties.job_message = event.message
        history.append(event.message)
        changed = True
    if changed:
        properties.job_event_history = json.dumps(history[-6:])
    return changed


def _event_history(serialized: str) -> list[str]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str)]


def _redraw_extension_ui() -> None:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {"VIEW_3D", "PROPERTIES", "STATUSBAR"}:
                area.tag_redraw()


def _apply_result(active: _ActiveJob, properties: Any, result: dict[str, Any]) -> None:
    state = str(result.get("state", JobState.INVALID_DATA.value))
    if state not in _TERMINAL_STATES:
        state = JobState.INVALID_DATA.value
    properties.job_active = False
    properties.active_job_mode = ""
    properties.job_state = state
    properties.job_progress = 1.0
    if state in {JobState.COMPLETE.value, JobState.COMPLETE_WITH_WARNINGS.value}:
        if active.mode == "delivery":
            elevation_count = len(result.get("elevation_paths", []))
            imagery_count = len(result.get("imagery_paths", []))
            terrain_count = len(result.get("processed_elevation", []))
            imagery_paths = tuple(Path(path) for path in result.get("imagery_paths", []))
            properties.imagery_size_mib = sum(
                path.stat().st_size for path in imagery_paths if path.is_file()
            ) / (1024 * 1024)
            properties.imagery_available = imagery_count > 0
            properties.delivery_ready = True
            properties.delivery_result_path = str(active.directory / "result.json")
            properties.delivery_summary = (
                f"Prepared {elevation_count} elevation, {imagery_count} imagery and "
                f"{terrain_count} terrain tile(s)"
            )
            warnings = result.get("warnings", [])
            properties.job_message = (
                str(warnings[0])
                if state == JobState.COMPLETE_WITH_WARNINGS.value and warnings
                else "Data download completed"
            )
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
            properties.imagery_available = False
            properties.imagery_size_mib = 0.0
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


def _job_from_properties(task_id: str, import_id: str, properties: Any) -> DiscoveryJob:
    try:
        region = RegionOfInterest.from_geojson_geometry(
            json.loads(properties.roi_geometry_json)
        )
        return DiscoveryJob(
            task_id=task_id,
            import_id=import_id,
            bounds=region.bounds,
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
            manual_tile_rows=(
                properties.manual_tile_rows
                if properties.tiling_mode == "MANUAL"
                else None
            ),
            manual_tile_columns=(
                properties.manual_tile_columns
                if properties.tiling_mode == "MANUAL"
                else None
            ),
            region=region,
        )
    except (JobFormatError, json.JSONDecodeError, ValueError) as exc:
        raise UserInputError(f"Cannot create the discovery job: {exc}") from exc


def _scene_properties(scene_name: str) -> Any | None:
    scene = bpy.data.scenes.get(scene_name)
    return None if scene is None else scene.blender_terrain_roi


def _stop_orphaned_job(active: _ActiveJob) -> None:
    if active.process.poll() is None:
        request_cancellation(active.directory)
        active.process.terminate()
