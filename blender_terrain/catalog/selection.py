"""Confirmed per-layer selections and immutable acquisition plans."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from ..core.roi import BBoxWGS84
from ..errors import SelectionError
from .candidates import CandidateSet, LicenseProfile
from .models import DatasetKind


class SelectionMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class FailurePolicy(StrEnum):
    FAIL_AND_ASK = "fail_and_ask"


@dataclass(frozen=True, slots=True)
class LayerRequest:
    kind: DatasetKind
    target_resolution_m: float | None = None
    temporal_policy: str | None = None

    def __post_init__(self) -> None:
        if self.target_resolution_m is not None and (
            not math.isfinite(self.target_resolution_m) or self.target_resolution_m <= 0
        ):
            raise SelectionError("Target resolution must be a positive finite number")


@dataclass(frozen=True, slots=True)
class AcquisitionRequest:
    roi: BBoxWGS84
    layers: tuple[LayerRequest, ...]
    license_profile: LicenseProfile = LicenseProfile.COMMERCIAL_SAFE

    def __post_init__(self) -> None:
        kinds = tuple(layer.kind for layer in self.layers)
        if not kinds:
            raise SelectionError("An acquisition request must contain at least one layer")
        if len(kinds) != len(set(kinds)):
            raise SelectionError("An acquisition request cannot repeat a layer kind")

    def layer(self, kind: DatasetKind) -> LayerRequest:
        for layer in self.layers:
            if layer.kind is kind:
                return layer
        raise KeyError(kind)


@dataclass(frozen=True, slots=True)
class ProductSelection:
    provider_id: str
    product_id: str
    kind: DatasetKind
    mode: SelectionMode
    confirmed_by_user: bool
    temporal_policy: str | None = None
    failure_policy: FailurePolicy = FailurePolicy.FAIL_AND_ASK

    def __post_init__(self) -> None:
        if not self.provider_id or not self.product_id:
            raise SelectionError("Product selection identity cannot be empty")


@dataclass(frozen=True, slots=True)
class SelectionBundle:
    selections: tuple[ProductSelection, ...]

    def __post_init__(self) -> None:
        kinds = tuple(selection.kind for selection in self.selections)
        if len(kinds) != len(set(kinds)):
            raise SelectionError("A selection bundle cannot repeat a layer kind")

    def for_kind(self, kind: DatasetKind) -> ProductSelection | None:
        for selection in self.selections:
            if selection.kind is kind:
                return selection
        return None


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    request: AcquisitionRequest
    selections: SelectionBundle

    def __post_init__(self) -> None:
        if not all(selection.confirmed_by_user for selection in self.selections.selections):
            raise SelectionError("Acquisition plans require confirmed selections")
        requested = {layer.kind for layer in self.request.layers}
        selected = {selection.kind for selection in self.selections.selections}
        if requested != selected:
            raise SelectionError("Acquisition plans require exactly one selection per layer")

    def is_current(
        self,
        request: AcquisitionRequest,
        selections: SelectionBundle,
    ) -> bool:
        """Return whether neither request settings nor selected products changed."""

        return self.request == request and self.selections == selections


def create_acquisition_plan(
    request: AcquisitionRequest,
    selections: SelectionBundle,
    candidate_sets: tuple[CandidateSet, ...],
) -> AcquisitionPlan:
    """Validate confirmed choices against candidate snapshots and lock the plan."""

    sets_by_kind = _candidate_sets_by_kind(candidate_sets)
    request_kinds = {layer.kind for layer in request.layers}
    selection_kinds = {selection.kind for selection in selections.selections}
    if selection_kinds != request_kinds:
        raise SelectionError("Selections must match every requested layer exactly")
    if set(sets_by_kind) != request_kinds:
        raise SelectionError("Candidate sets must match every requested layer exactly")

    for selection in selections.selections:
        candidates = sets_by_kind[selection.kind]
        if (
            candidates.roi != request.roi
            or candidates.license_profile is not request.license_profile
        ):
            raise SelectionError("Candidate set is stale for the acquisition request")
        if not selection.confirmed_by_user:
            raise SelectionError("Confirm every product selection before creating a plan")
        if selection.temporal_policy != request.layer(selection.kind).temporal_policy:
            raise SelectionError("Selection temporal policy does not match the request")
        candidate = next(
            (
                item
                for item in candidates.valid
                if item.product.id == selection.product_id
                and item.product.provider_id == selection.provider_id
            ),
            None,
        )
        if candidate is None:
            raise SelectionError("Selected product is not a valid candidate")
        if selection.mode is SelectionMode.AUTO and candidate != candidates.recommended:
            raise SelectionError("Auto selection must use the displayed recommendation")
    return AcquisitionPlan(request, selections)


def _candidate_sets_by_kind(
    candidate_sets: tuple[CandidateSet, ...],
) -> dict[DatasetKind, CandidateSet]:
    result: dict[DatasetKind, CandidateSet] = {}
    for candidates in candidate_sets:
        if candidates.kind in result:
            raise SelectionError("Candidate sets cannot repeat a layer kind")
        result[candidates.kind] = candidates
    return result
