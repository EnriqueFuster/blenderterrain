"""Coarse operational envelopes for selecting Spain's supported datum family."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..errors import NoCoverageError
from .roi import BBoxWGS84


class TerritoryGroup(StrEnum):
    """Territory groups that use different native horizontal datums."""

    ETRS89 = "ETRS89"
    CANARY_ISLANDS = "CANARY_ISLANDS"


@dataclass(frozen=True, slots=True)
class TerritoryEnvelope:
    """A deliberately broad envelope used before provider coverage discovery."""

    group: TerritoryGroup
    bounds: BBoxWGS84

    def contains(self, candidate: BBoxWGS84) -> bool:
        """Return whether the complete ROI stays inside this operational envelope."""

        return (
            candidate.west >= self.bounds.west
            and candidate.south >= self.bounds.south
            and candidate.east <= self.bounds.east
            and candidate.north <= self.bounds.north
        )


_TERRITORY_ENVELOPES = (
    TerritoryEnvelope(TerritoryGroup.ETRS89, BBoxWGS84(-10.0, 35.0, 5.0, 44.5)),
    TerritoryEnvelope(
        TerritoryGroup.CANARY_ISLANDS,
        BBoxWGS84(-18.5, 27.0, -13.0, 30.5),
    ),
)


def classify_territory_envelope(bounds: BBoxWGS84) -> TerritoryGroup:
    """Select a datum family without claiming exact administrative coverage."""

    matches = [envelope.group for envelope in _TERRITORY_ENVELOPES if envelope.contains(bounds)]
    if len(matches) != 1:
        raise NoCoverageError(
            "ROI is outside the supported Spain planning envelopes; exact coverage "
            "is confirmed during CNIG discovery"
        )
    return matches[0]
