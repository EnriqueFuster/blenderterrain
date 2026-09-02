"""Compatibility access to the former Spanish territory helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .roi import BBoxWGS84

if TYPE_CHECKING:
    from ..providers.spain_crs import TerritoryEnvelope, TerritoryGroup

__all__ = ["TerritoryEnvelope", "TerritoryGroup", "classify_territory_envelope"]


def __getattr__(name: str) -> Any:
    if name in {"TerritoryEnvelope", "TerritoryGroup"}:
        from ..providers import spain_crs

        return getattr(spain_crs, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def classify_territory_envelope(bounds: BBoxWGS84) -> TerritoryGroup:
    """Call the former Spanish territory classifier retained for compatibility."""

    from ..providers.spain_crs import classify_territory_envelope as classify

    return classify(bounds)
