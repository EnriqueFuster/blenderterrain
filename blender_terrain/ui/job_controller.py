"""Launch and monitor BlenderTerrain background workers from Blender's UI."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import bpy

from ..catalog import (
    AcquisitionPlan,
    AcquisitionRequest,
    DatasetKind,
    LayerRequest,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
    create_acquisition_plan,
    discover_candidates,
    load_bundled_catalog,
)
from ..core import (
    RESOURCE_PROFILES,
    RegionOfInterest,
    inspect_local_imagery,
    resolve_local_elevation_paths,
)
from ..errors import JobFormatError, UserInputError
from ..jobs.acquisition_job import AcquisitionJob
from ..jobs.models import DiscoveryJob, JobState
from ..jobs.storage import (
    read_acquisition_job,
    read_discovery_job,
    read_progress_events,
    request_cancellation,
    write_acquisition_job,
    write_discovery_job,
)
from ..models import DatasetProduct
from ..providers.copernicus_dem import GLO30_PRODUCT_ID, glo30_tiles_for_roi
from ..providers.gebco import PRODUCT_ID as GEBCO_PRODUCT_ID
from ..providers.gedtm30 import GEDTM30_PRODUCT_ID
from ..providers.worldcover import PRODUCT_ID as WORLDCOVER_PRODUCT_ID

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
        elif mode == "availability":
            properties.product_availability_json = "[]"
            properties.product_availability_summary = ""
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
    if properties.elevation_source == "LOCAL":
        _start_worker(
            context,
            properties,
            "local_discovery",
            "Starting local raster validation",
        )
        return
    product = load_bundled_catalog().product(properties.product)
    if product.provider_id != "ign_cnig":
        region = RegionOfInterest.from_geojson_geometry(json.loads(properties.roi_geometry_json))
        count = (
            len(glo30_tiles_for_roi(region.bounds))
            if properties.product == GLO30_PRODUCT_ID
            else 0
        )
        properties.discovered_file_count = count
        if properties.product == GLO30_PRODUCT_ID:
            properties.discovery_summary = f"{count} Copernicus GLO-30 tile(s) required"
        elif properties.product == GEDTM30_PRODUCT_ID:
            properties.discovery_summary = "GEDTM30 elevation and uncertainty windows required"
        else:
            properties.discovery_summary = (
                f"{product.name} WMS windows required; valid pixels are confirmed during download"
            )
        if properties.bathymetry_mode == "GEBCO":
            properties.discovery_summary += "; GEBCO elevation and quality windows required"
        if properties.imagery_product != "NONE":
            properties.discovery_summary += f"; {properties.imagery_product} imagery required"
        properties.discovery_ready = True
        properties.job_state = JobState.COMPLETE.value
        properties.job_progress = 1.0
        properties.job_message = "Selected raster sources resolved from the confirmed ROI"
        return
    _start_worker(context, properties, "discovery", "Starting background source discovery")


def start_delivery(context: bpy.types.Context) -> None:
    """Launch elevation and optional PNOA delivery in a background worker."""

    properties = context.scene.blender_terrain_roi
    if not properties.is_valid or not properties.discovery_ready:
        raise UserInputError("Discover the current sources before downloading data")
    properties.delivery_summary = ""
    if properties.elevation_source != "LOCAL":
        _start_acquisition_worker(context, properties)
        return
    _start_worker(context, properties, "local_delivery", "Starting local raster processing")


def _start_acquisition_worker(context: bpy.types.Context, properties: Any) -> None:
    """Persist the confirmed global product choice and launch its portable worker."""

    global _active_job
    if _active_job is not None:
        raise UserInputError("Another BlenderTerrain job is already running")
    if not bpy.app.online_access:
        raise UserInputError("Blender online access is disabled in Preferences")
    region = RegionOfInterest.from_geojson_geometry(json.loads(properties.roi_geometry_json))
    plan = _acquisition_plan_from_properties(properties, region)
    product = load_bundled_catalog().product(properties.product)
    cache_directory = configured_cache_directory(context)
    task_id = str(uuid4())
    if not properties.import_id:
        properties.import_id = str(uuid4())
    job_directory = cache_directory / "jobs" / task_id
    elevation_limit, imagery_limit = RESOURCE_PROFILES[properties.resource_profile]
    write_acquisition_job(
        job_directory / "job.json",
        AcquisitionJob(
            task_id,
            properties.import_id,
            plan,
            elevation_limit,
            region,
            properties.manual_tile_rows if properties.tiling_mode == "MANUAL" else None,
            properties.manual_tile_columns if properties.tiling_mode == "MANUAL" else None,
            imagery_limit,
        ),
    )
    _launch_worker(
        context,
        properties,
        "acquisition",
        f"Starting {product.name} acquisition",
        job_directory,
    )


def _acquisition_plan_from_properties(properties: Any, region: RegionOfInterest) -> AcquisitionPlan:
    """Build the confirmed global plan represented by the current UI controls."""

    catalog = load_bundled_catalog()
    product = catalog.product(properties.product)
    layer = LayerRequest(
        product.capabilities.kind,
        None
        if properties.elevation_resolution == "AUTO"
        else float(properties.elevation_resolution),
    )
    layers = [layer]
    selections = [
        ProductSelection(
            product.provider_id,
            product.id,
            product.capabilities.kind,
            SelectionMode.MANUAL,
            True,
        )
    ]
    candidate_sets = [discover_candidates(catalog, region.bounds, product.capabilities.kind)]
    if properties.bathymetry_mode == "GEBCO":
        bathymetry_product = catalog.product(GEBCO_PRODUCT_ID)
        bathymetry_layer = LayerRequest(DatasetKind.BATHYMETRY, 463.0)
        layers.append(bathymetry_layer)
        selections.append(
            ProductSelection(
                bathymetry_product.provider_id,
                bathymetry_product.id,
                DatasetKind.BATHYMETRY,
                SelectionMode.MANUAL,
                True,
            )
        )
        candidate_sets.append(discover_candidates(catalog, region.bounds, DatasetKind.BATHYMETRY))
    if properties.imagery_product != "NONE":
        is_global = properties.imagery_product == WORLDCOVER_PRODUCT_ID
        imagery_product = catalog.product(properties.imagery_product)
        imagery_layer = LayerRequest(
            DatasetKind.IMAGERY,
            properties.selected_imagery_gsd
            or imagery_product.capabilities.native_resolution_m,
            "2021" if is_global else None,
        )
        layers.append(imagery_layer)
        selections.append(
            ProductSelection(
                imagery_product.provider_id,
                imagery_product.id,
                DatasetKind.IMAGERY,
                SelectionMode.MANUAL,
                True,
                "2021" if is_global else None,
            )
        )
        candidate_sets.append(discover_candidates(catalog, region.bounds, DatasetKind.IMAGERY))
    request = AcquisitionRequest(region.bounds, tuple(layers))
    return create_acquisition_plan(
        request,
        SelectionBundle(tuple(selections)),
        tuple(candidate_sets),
    )


def start_availability(context: bpy.types.Context) -> None:
    """Check all official elevation products for the current ROI in the background."""

    properties = context.scene.blender_terrain_roi
    if not properties.roi_geometry_json:
        raise UserInputError("Validate or select the ROI before checking product availability")
    properties.product_availability_json = "[]"
    properties.product_availability_summary = ""
    _start_worker(
        context,
        properties,
        "availability",
        "Starting product availability check",
    )


def _start_worker(context: bpy.types.Context, properties: Any, mode: str, message: str) -> None:
    global _active_job
    if _active_job is not None:
        raise UserInputError("Another BlenderTerrain job is already running")
    cache_directory = configured_cache_directory(context)
    task_id = str(uuid4())
    if not properties.import_id:
        properties.import_id = str(uuid4())
    job_directory = cache_directory / "jobs" / task_id
    job_path = job_directory / "job.json"
    job = _job_from_properties(task_id, properties.import_id, properties)
    if _job_requires_network(job, mode) and not bpy.app.online_access:
        raise UserInputError("Blender online access is disabled in Preferences")
    write_discovery_job(job_path, job)
    _launch_worker(context, properties, mode, message, job_directory)


def retry_last_job(context: bpy.types.Context) -> None:
    """Relaunch the last persisted request with a new task identity."""

    properties = context.scene.blender_terrain_roi
    if _active_job is not None:
        raise UserInputError("Another BlenderTerrain job is already running")
    previous_path = Path(properties.last_job_path)
    cache_directory = configured_cache_directory(context)
    jobs_directory = (cache_directory / "jobs").resolve()
    try:
        resolved_previous = previous_path.expanduser().resolve(strict=True)
        resolved_previous.relative_to(jobs_directory)
    except (OSError, ValueError) as exc:
        raise UserInputError("The previous job is no longer available in the cache") from exc
    mode = properties.last_job_mode
    if mode not in {
        "discovery",
        "availability",
        "delivery",
        "acquisition",
        "local_discovery",
        "local_delivery",
    }:
        raise UserInputError("The previous job mode cannot be retried")
    if mode == "acquisition":
        previous_acquisition = read_acquisition_job(resolved_previous)
        if not bpy.app.online_access:
            raise UserInputError("Blender online access is disabled in Preferences")
        task_id = str(uuid4())
        job_directory = cache_directory / "jobs" / task_id
        write_acquisition_job(
            job_directory / "job.json",
            replace(previous_acquisition, task_id=task_id),
        )
        properties.import_id = previous_acquisition.import_id
        _launch_worker(
            context,
            properties,
            mode,
            "Retrying previous acquisition job",
            job_directory,
        )
        return
    previous = read_discovery_job(resolved_previous)
    if _job_requires_network(previous, mode) and not bpy.app.online_access:
        raise UserInputError("Blender online access is disabled in Preferences")
    task_id = str(uuid4())
    job_directory = cache_directory / "jobs" / task_id
    write_discovery_job(job_directory / "job.json", replace(previous, task_id=task_id))
    properties.import_id = previous.import_id
    _launch_worker(
        context,
        properties,
        mode,
        f"Retrying previous {mode} job",
        job_directory,
    )


def _job_requires_network(job: DiscoveryJob, mode: str) -> bool:
    return (
        mode == "availability"
        or mode == "discovery"
        or not job.local_elevation_paths
        or (job.use_imagery and job.local_imagery_path is None)
    )


def _launch_worker(
    context: bpy.types.Context,
    properties: Any,
    mode: str,
    message: str,
    job_directory: Path,
) -> None:
    global _active_job
    job_path = job_directory / "job.json"

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
    if mode != "discovery":
        command.append(mode)
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
    properties.job_event_history = json.dumps([{"message": message, "progress": 0.0}])
    properties.last_job_path = str(job_path)
    properties.last_job_mode = mode
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
        events, active.event_offset = read_progress_events(events_path, active.event_offset)
    except JobFormatError:
        return False
    changed = False
    history = _event_history(properties.job_event_history)
    for event in events:
        if event.sequence <= active.last_sequence:
            continue
        active.last_sequence = event.sequence
        displayed_progress = max(properties.job_progress, event.progress)
        properties.job_state = event.state.value
        properties.job_progress = displayed_progress
        properties.job_message = event.message
        history.append({"message": event.message, "progress": displayed_progress})
        changed = True
    if changed:
        properties.job_event_history = json.dumps(history[-6:])
    return changed


def _event_history(serialized: str) -> list[dict[str, object]]:
    try:
        values = json.loads(serialized)
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    history: list[dict[str, object]] = []
    for value in values:
        if isinstance(value, str):
            history.append({"message": value, "progress": None})
        elif (
            isinstance(value, dict)
            and isinstance(value.get("message"), str)
            and (value.get("progress") is None or isinstance(value.get("progress"), (int, float)))
        ):
            history.append({"message": value["message"], "progress": value.get("progress")})
    return history


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
        if active.mode == "availability":
            availability = result.get("availability", [])
            if not isinstance(availability, list):
                availability = []
            properties.product_availability_json = json.dumps(availability)
            available_count = sum(
                entry.get("status") == "AVAILABLE"
                for entry in availability
                if isinstance(entry, dict)
            )
            properties.product_availability_summary = (
                f"{available_count} of {len(availability)} products available for this ROI"
            )
            warnings = result.get("warnings", [])
            properties.job_message = (
                str(warnings[0]) if warnings else "Product availability check completed"
            )
            return
        if active.mode in {"delivery", "acquisition", "local_delivery"}:
            elevation_count = len(result.get("elevation_paths", []))
            imagery_count = len(result.get("imagery_paths", []))
            bathymetry_count = len(result.get("bathymetry", []))
            terrain_count = len(result.get("processed_elevation", []))
            imagery_paths = tuple(Path(path) for path in result.get("imagery_paths", []))
            properties.imagery_size_mib = sum(
                path.stat().st_size for path in imagery_paths if path.is_file()
            ) / (1024 * 1024)
            properties.imagery_available = imagery_count > 0
            properties.delivery_ready = True
            properties.show_creation_section = True
            properties.delivery_result_path = str(active.directory / "result.json")
            properties.delivery_summary = (
                f"Prepared {elevation_count} elevation, {bathymetry_count} bathymetry, "
                f"{imagery_count} imagery and {terrain_count} terrain tile(s)"
            )
            timings = result.get("timings_seconds", {})
            reuse = result.get("cache_reuse", {})
            if not isinstance(timings, dict):
                timings = {}
            if not isinstance(reuse, dict):
                reuse = {}
            total_seconds = float(timings.get("total", 0.0) or 0.0)
            reused = int(reuse.get("elevation_files", 0) or 0) + int(
                reuse.get("imagery_files", 0) or 0
            )
            properties.delivery_metrics_summary = (
                f"Completed in {total_seconds:.1f} s; reused {reused} cached file(s)"
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
        parts = [f"{count} elevation source file(s){size_text}"]
        if properties.bathymetry_mode == "GEBCO":
            parts.append("GEBCO bathymetry")
        if properties.imagery_product != "NONE":
            parts.append(f"{properties.imagery_product} imagery")
        properties.discovery_summary = "; ".join(parts)
        properties.discovery_ready = True
        properties.job_message = "Source discovery completed"
    else:
        if active.mode in {"discovery", "local_discovery"}:
            properties.discovery_summary = ""
            properties.discovery_ready = False
        elif active.mode == "availability":
            properties.product_availability_json = "[]"
            properties.product_availability_summary = ""
        else:
            properties.delivery_ready = False
            properties.delivery_summary = ""
            properties.imagery_available = False
            properties.imagery_size_mib = 0.0
        properties.job_message = str(result.get("error", f"Discovery ended as {state}"))


def configured_cache_directory(context: bpy.types.Context) -> Path:
    """Return the configured cache root after creating and validating it."""
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
        region = RegionOfInterest.from_geojson_geometry(json.loads(properties.roi_geometry_json))
        local_paths = (
            _local_elevation_paths(properties.local_elevation_path)
            if properties.elevation_source == "LOCAL"
            else ()
        )
        local_imagery = (
            inspect_local_imagery(Path(bpy.path.abspath(properties.local_imagery_path)))
            if properties.elevation_source == "LOCAL" and properties.use_local_imagery
            else None
        )
        elevation_limit, imagery_limit = RESOURCE_PROFILES[properties.resource_profile]
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
            use_imagery=(
                properties.imagery_product != "NONE"
                if properties.elevation_source == "CNIG"
                else local_imagery is not None
            ),
            imagery_gsd_metres=(
                None
                if properties.elevation_source == "LOCAL" or properties.imagery_gsd == "AUTO"
                else float(properties.imagery_gsd)
            ),
            manual_tile_rows=(
                properties.manual_tile_rows if properties.tiling_mode == "MANUAL" else None
            ),
            manual_tile_columns=(
                properties.manual_tile_columns if properties.tiling_mode == "MANUAL" else None
            ),
            region=region,
            local_elevation_paths=local_paths,
            local_imagery_path=(None if local_imagery is None else str(local_imagery.path)),
            local_imagery_bounds=(None if local_imagery is None else local_imagery.bounds),
            local_imagery_width=(None if local_imagery is None else local_imagery.width),
            local_imagery_height=(None if local_imagery is None else local_imagery.height),
            maximum_elevation_samples=elevation_limit,
            maximum_imagery_pixels=imagery_limit,
        )
    except (JobFormatError, json.JSONDecodeError, ValueError) as exc:
        raise UserInputError(f"Cannot create the discovery job: {exc}") from exc


def _local_elevation_paths(raw_path: str) -> tuple[str, ...]:
    return tuple(str(path) for path in resolve_local_elevation_paths(bpy.path.abspath(raw_path)))


def _scene_properties(scene_name: str) -> Any | None:
    scene = bpy.data.scenes.get(scene_name)
    return None if scene is None else scene.blender_terrain_roi


def _stop_orphaned_job(active: _ActiveJob) -> None:
    if active.process.poll() is None:
        request_cancellation(active.directory)
        active.process.terminate()
