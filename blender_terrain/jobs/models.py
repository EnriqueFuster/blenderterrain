"""Versioned models exchanged between Blender and a background worker."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from ..core.roi import BBoxWGS84
from ..errors import JobFormatError
from ..models import DatasetProduct

JOB_SCHEMA_VERSION = 1


class JobState(StrEnum):
    """Observable states emitted by a discovery worker."""

    VALIDATING = "VALIDATING"
    DISCOVERING = "DISCOVERING"
    COMPLETE = "COMPLETE"
    CANCELLED = "CANCELLED"
    NO_COVERAGE = "NO_COVERAGE"
    PROVIDER_CHANGED = "PROVIDER_CHANGED"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_DATA = "INVALID_DATA"


@dataclass(frozen=True, slots=True)
class DiscoveryJob:
    """Inputs needed to reconstruct an offline plan and discover CNIG sources."""

    job_id: str
    bounds: BBoxWGS84
    product: DatasetProduct
    elevation_resolution_metres: float | None
    use_imagery: bool
    imagery_gsd_metres: float | None

    def __post_init__(self) -> None:
        try:
            UUID(self.job_id)
        except ValueError as exc:
            raise JobFormatError("Job identifier must be a UUID") from exc

    def to_dict(self) -> dict[str, Any]:
        """Serialize using only JSON-compatible stable fields."""

        return {
            "schema_version": JOB_SCHEMA_VERSION,
            "job_id": self.job_id,
            "bounds": asdict(self.bounds),
            "product": self.product.value,
            "elevation_resolution_metres": self.elevation_resolution_metres,
            "use_imagery": self.use_imagery,
            "imagery_gsd_metres": self.imagery_gsd_metres,
        }

    @classmethod
    def from_dict(cls, payload: object) -> DiscoveryJob:
        """Validate an untrusted JSON value against schema version one."""

        if not isinstance(payload, dict) or payload.get("schema_version") != JOB_SCHEMA_VERSION:
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
            use_imagery = payload["use_imagery"]
            if not isinstance(use_imagery, bool):
                raise TypeError
            job_id = payload["job_id"]
            if not isinstance(job_id, str):
                raise TypeError
        except (KeyError, TypeError, ValueError) as exc:
            raise JobFormatError("Job JSON contains invalid fields") from exc
        return cls(
            job_id=job_id,
            bounds=bounds,
            product=product,
            elevation_resolution_metres=elevation_resolution,
            use_imagery=use_imagery,
            imagery_gsd_metres=imagery_gsd,
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


def _optional_finite_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError
    return converted
