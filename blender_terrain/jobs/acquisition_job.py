"""Persisted protocol for a confirmed multi-provider acquisition plan."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from ..catalog import (
    AcquisitionPlan,
    AcquisitionRequest,
    DatasetKind,
    FailurePolicy,
    LayerRequest,
    LicenseProfile,
    ProductSelection,
    SelectionBundle,
    SelectionMode,
)
from ..core.roi import BBoxWGS84
from ..errors import JobFormatError

ACQUISITION_JOB_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class AcquisitionJob:
    """A confirmed acquisition plan ready for background execution."""

    task_id: str
    import_id: str
    plan: AcquisitionPlan
    maximum_elevation_samples: int = 16_777_216

    def __post_init__(self) -> None:
        try:
            UUID(self.task_id)
            UUID(self.import_id)
        except ValueError as exc:
            raise JobFormatError("Task and import identifiers must be UUIDs") from exc
        if self.maximum_elevation_samples <= 0:
            raise JobFormatError("Maximum elevation samples must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ACQUISITION_JOB_SCHEMA_VERSION,
            "task_id": self.task_id,
            "import_id": self.import_id,
            "maximum_elevation_samples": self.maximum_elevation_samples,
            "plan": {
                "roi": asdict(self.plan.request.roi),
                "license_profile": self.plan.request.license_profile.value,
                "layers": [
                    {
                        "kind": layer.kind.value,
                        "target_resolution_m": layer.target_resolution_m,
                        "temporal_policy": layer.temporal_policy,
                    }
                    for layer in self.plan.request.layers
                ],
                "selections": [
                    {
                        "provider_id": selection.provider_id,
                        "product_id": selection.product_id,
                        "kind": selection.kind.value,
                        "mode": selection.mode.value,
                        "confirmed_by_user": selection.confirmed_by_user,
                        "temporal_policy": selection.temporal_policy,
                        "failure_policy": selection.failure_policy.value,
                    }
                    for selection in self.plan.selections.selections
                ],
            },
        }

    @classmethod
    def from_dict(cls, payload: object) -> AcquisitionJob:
        """Validate and reconstruct an immutable confirmed plan."""

        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise JobFormatError("Unsupported acquisition job schema")
        try:
            raw_plan = payload["plan"]
            raw_roi = raw_plan["roi"]
            request = AcquisitionRequest(
                BBoxWGS84(
                    float(raw_roi["west"]),
                    float(raw_roi["south"]),
                    float(raw_roi["east"]),
                    float(raw_roi["north"]),
                ),
                tuple(
                    LayerRequest(
                        DatasetKind(layer["kind"]),
                        _optional_float(layer.get("target_resolution_m")),
                        _optional_string(layer.get("temporal_policy")),
                    )
                    for layer in raw_plan["layers"]
                ),
                LicenseProfile(raw_plan["license_profile"]),
            )
            selections = SelectionBundle(
                tuple(
                    ProductSelection(
                        selection["provider_id"],
                        selection["product_id"],
                        DatasetKind(selection["kind"]),
                        SelectionMode(selection["mode"]),
                        selection["confirmed_by_user"],
                        _optional_string(selection.get("temporal_policy")),
                        FailurePolicy(selection["failure_policy"]),
                    )
                    for selection in raw_plan["selections"]
                )
            )
            task_id = payload["task_id"]
            import_id = payload["import_id"]
            maximum_samples = payload["maximum_elevation_samples"]
            if (
                not isinstance(task_id, str)
                or not isinstance(import_id, str)
                or isinstance(maximum_samples, bool)
                or not isinstance(maximum_samples, int)
                or any(
                    not isinstance(selection.confirmed_by_user, bool)
                    for selection in selections.selections
                )
            ):
                raise TypeError
            return cls(
                task_id,
                import_id,
                AcquisitionPlan(request, selections),
                maximum_samples,
            )
        except (KeyError, TypeError, ValueError, JobFormatError) as exc:
            raise JobFormatError("Acquisition job contains invalid fields") from exc


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError
    return float(value)


def _optional_string(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        raise TypeError
    return value
