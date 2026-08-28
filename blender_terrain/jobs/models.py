"""Versioned models exchanged between Blender and a background worker."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from ..core.roi import BBoxWGS84, RegionOfInterest
from ..errors import JobFormatError, UserInputError
from ..models import DatasetProduct, ProjectedBounds

JOB_SCHEMA_VERSION = 7
SUPPORTED_JOB_SCHEMA_VERSIONS = frozenset({6, JOB_SCHEMA_VERSION})
RESULT_SCHEMA_VERSION = 2


class JobState(StrEnum):
    """Observable states emitted by a discovery worker."""

    VALIDATING = "VALIDATING"
    DISCOVERING = "DISCOVERING"
    DOWNLOADING_ELEVATION = "DOWNLOADING_ELEVATION"
    DOWNLOADING_IMAGERY = "DOWNLOADING_IMAGERY"
    PROCESSING_ELEVATION = "PROCESSING_ELEVATION"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    CANCELLED = "CANCELLED"
    NO_COVERAGE = "NO_COVERAGE"
    PROVIDER_CHANGED = "PROVIDER_CHANGED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_DATA = "INVALID_DATA"


@dataclass(frozen=True, slots=True)
class DiscoveryJob:
    """Inputs needed to reconstruct an offline plan and discover CNIG sources."""

    task_id: str
    import_id: str
    bounds: BBoxWGS84
    product: DatasetProduct
    elevation_resolution_metres: float | None
    use_imagery: bool
    imagery_gsd_metres: float | None
    manual_tile_rows: int | None = None
    manual_tile_columns: int | None = None
    region: RegionOfInterest | None = None
    local_elevation_paths: tuple[str, ...] = ()
    local_imagery_path: str | None = None
    local_imagery_bounds: ProjectedBounds | None = None
    local_imagery_width: int | None = None
    local_imagery_height: int | None = None
    maximum_elevation_samples: int = 16_777_216
    maximum_imagery_pixels: int = 67_108_864

    def __post_init__(self) -> None:
        try:
            UUID(self.task_id)
            UUID(self.import_id)
        except ValueError as exc:
            raise JobFormatError("Task and import identifiers must be UUIDs") from exc
        if self.region is not None and self.region.bounds != self.bounds:
            raise JobFormatError("ROI geometry bounds do not match the job bounds")
        if any(not path for path in self.local_elevation_paths):
            raise JobFormatError("Local elevation paths must not be empty")
        imagery_values = (
            self.local_imagery_path,
            self.local_imagery_bounds,
            self.local_imagery_width,
            self.local_imagery_height,
        )
        if any(value is None for value in imagery_values) != all(
            value is None for value in imagery_values
        ):
            raise JobFormatError("Local imagery metadata must be provided together")
        if self.local_imagery_path is not None and (
            not self.local_elevation_paths
            or not self.use_imagery
            or self.local_imagery_width is None
            or self.local_imagery_height is None
            or self.local_imagery_width <= 0
            or self.local_imagery_height <= 0
        ):
            raise JobFormatError("Local imagery requires local elevation and valid dimensions")
        if self.maximum_elevation_samples <= 0 or self.maximum_imagery_pixels <= 0:
            raise JobFormatError("Job resource limits must be positive")

    def to_dict(self) -> dict[str, Any]:
        """Serialize using only JSON-compatible stable fields."""

        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "task_id": self.task_id,
            "import_id": self.import_id,
            "bounds": asdict(self.bounds),
            "product": self.product.value,
            "elevation_resolution_metres": self.elevation_resolution_metres,
            "use_imagery": self.use_imagery,
            "imagery_gsd_metres": self.imagery_gsd_metres,
            "manual_tile_rows": self.manual_tile_rows,
            "manual_tile_columns": self.manual_tile_columns,
            "roi_geometry_wgs84": (
                None if self.region is None else self.region.to_geojson_geometry()
            ),
            "local_elevation_paths": list(self.local_elevation_paths),
            "local_imagery_path": self.local_imagery_path,
            "local_imagery_bounds": (
                None if self.local_imagery_bounds is None else asdict(self.local_imagery_bounds)
            ),
            "local_imagery_width": self.local_imagery_width,
            "local_imagery_height": self.local_imagery_height,
            "maximum_elevation_samples": self.maximum_elevation_samples,
            "maximum_imagery_pixels": self.maximum_imagery_pixels,
        }

    @classmethod
    def from_dict(cls, payload: object) -> DiscoveryJob:
        """Validate an untrusted JSON value against a supported job schema."""

        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") not in SUPPORTED_JOB_SCHEMA_VERSIONS
        ):
            raise JobFormatError("Unsupported or missing job schema version")
        try:
            raw_bounds = payload["bounds"]
            if not isinstance(raw_bounds, dict):
                raise TypeError
            bounds = BBoxWGS84(
                float(raw_bounds["west"]),
                float(raw_bounds["south"]),
                float(raw_bounds["east"]),
                float(raw_bounds["north"]),
            )
            product = DatasetProduct(payload["product"])
            elevation_resolution = _optional_finite_float(
                payload.get("elevation_resolution_metres")
            )
            imagery_gsd = _optional_finite_float(payload.get("imagery_gsd_metres"))
            manual_tile_rows = _optional_positive_int(payload.get("manual_tile_rows"))
            manual_tile_columns = _optional_positive_int(
                payload.get("manual_tile_columns")
            )
            raw_region = payload.get("roi_geometry_wgs84")
            region = (
                None
                if raw_region is None
                else RegionOfInterest.from_geojson_geometry(raw_region)
            )
            use_imagery = payload["use_imagery"]
            if not isinstance(use_imagery, bool):
                raise TypeError
            task_id = payload["task_id"]
            import_id = payload["import_id"]
            if not isinstance(task_id, str) or not isinstance(import_id, str):
                raise TypeError
            raw_local_paths = payload.get("local_elevation_paths", [])
            if not isinstance(raw_local_paths, list) or not all(
                isinstance(path, str) and path for path in raw_local_paths
            ):
                raise TypeError
            maximum_elevation_samples = _positive_int(
                payload.get("maximum_elevation_samples")
            )
            maximum_imagery_pixels = _positive_int(payload.get("maximum_imagery_pixels"))
            local_imagery_path = payload.get("local_imagery_path")
            raw_imagery_bounds = payload.get("local_imagery_bounds")
            local_imagery_width = payload.get("local_imagery_width")
            local_imagery_height = payload.get("local_imagery_height")
            if local_imagery_path is not None and not isinstance(local_imagery_path, str):
                raise TypeError
            local_imagery_bounds = (
                None
                if raw_imagery_bounds is None
                else _projected_bounds(raw_imagery_bounds)
            )
            if local_imagery_width is not None:
                local_imagery_width = _positive_int(local_imagery_width)
            if local_imagery_height is not None:
                local_imagery_height = _positive_int(local_imagery_height)
        except (KeyError, TypeError, ValueError, UserInputError) as exc:
            raise JobFormatError("Job JSON contains invalid fields") from exc
        return cls(
            task_id=task_id,
            import_id=import_id,
            bounds=bounds,
            product=product,
            elevation_resolution_metres=elevation_resolution,
            use_imagery=use_imagery,
            imagery_gsd_metres=imagery_gsd,
            manual_tile_rows=manual_tile_rows,
            manual_tile_columns=manual_tile_columns,
            region=region,
            local_elevation_paths=tuple(raw_local_paths),
            local_imagery_path=local_imagery_path,
            local_imagery_bounds=local_imagery_bounds,
            local_imagery_width=local_imagery_width,
            local_imagery_height=local_imagery_height,
            maximum_elevation_samples=maximum_elevation_samples,
            maximum_imagery_pixels=maximum_imagery_pixels,
        )


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One append-only progress event emitted by the worker."""

    sequence: int
    state: JobState
    progress: float
    message: str

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise JobFormatError("Event sequence must be non-negative")
        if not math.isfinite(self.progress) or not 0.0 <= self.progress <= 1.0:
            raise JobFormatError("Event progress must be between zero and one")
        if not self.message:
            raise JobFormatError("Event message must not be empty")

    def to_dict(self) -> dict[str, Any]:
        """Serialize the event as one JSON Lines record."""

        return {
            "sequence": self.sequence,
            "state": self.state.value,
            "progress": self.progress,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, payload: object) -> ProgressEvent:
        """Validate one persisted progress event."""

        if not isinstance(payload, dict):
            raise JobFormatError("Progress event must be a JSON object")
        try:
            sequence = payload["sequence"]
            state = JobState(payload["state"])
            progress = payload["progress"]
            message = payload["message"]
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise TypeError
            if isinstance(progress, bool) or not isinstance(progress, (int, float)):
                raise TypeError
            if not isinstance(message, str):
                raise TypeError
            return cls(sequence, state, float(progress), message)
        except (KeyError, TypeError, ValueError) as exc:
            raise JobFormatError("Progress event contains invalid fields") from exc


def _optional_finite_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError
    return converted


def _optional_positive_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64:
        raise ValueError
    return value


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError
    return value


def _projected_bounds(value: object) -> ProjectedBounds:
    if not isinstance(value, dict):
        raise TypeError
    return ProjectedBounds(
        float(value["west"]),
        float(value["south"]),
        float(value["east"]),
        float(value["north"]),
        int(value["epsg"]),
    )
