"""Select native projected work areas for Spanish elevation products."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.crs import ProjectedWorkArea, crs_from_epsg
from ..core.roi import BBoxWGS84
from ..errors import NoCoverageError


class TerritoryGroup(StrEnum):
    """Spanish territory groups that use different horizontal datums."""

    ETRS89 = "ETRS89"
    CANARY_ISLANDS = "CANARY_ISLANDS"


@dataclass(frozen=True, slots=True)
class TerritoryEnvelope:
    """Broad envelope used before exact provider coverage discovery."""

    group: TerritoryGroup
    bounds: BBoxWGS84

    def contains(self, candidate: BBoxWGS84) -> bool:
        return (
            candidate.west >= self.bounds.west
            and candidate.south >= self.bounds.south
            and candidate.east <= self.bounds.east
            and candidate.north <= self.bounds.north
        )


_TERRITORY_ENVELOPES = (
    TerritoryEnvelope(TerritoryGroup.ETRS89, BBoxWGS84(-10.0, 35.0, 5.0, 44.5)),
    TerritoryEnvelope(TerritoryGroup.CANARY_ISLANDS, BBoxWGS84(-18.5, 27.0, -13.0, 30.5)),
)

_ZONE_LONGITUDE_RANGES = {
    28: (-18.0, -12.0),
    29: (-12.0, -6.0),
    30: (-6.0, 0.0),
    31: (0.0, 6.0),
}


def classify_territory_envelope(bounds: BBoxWGS84) -> TerritoryGroup:
    """Select a Spanish datum family without claiming exact data coverage."""

    matches = [envelope.group for envelope in _TERRITORY_ENVELOPES if envelope.contains(bounds)]
    if len(matches) != 1:
        raise NoCoverageError(
            "ROI is outside the supported Spain planning envelopes; exact coverage "
            "is confirmed during CNIG discovery"
        )
    return matches[0]


def split_spain_bbox_by_utm_zone(bounds: BBoxWGS84) -> tuple[ProjectedWorkArea, ...]:
    """Split a Spanish ROI using its native datum and supported UTM zones."""

    territory = classify_territory_envelope(bounds)
    supported_zones = (28,) if territory is TerritoryGroup.CANARY_ISLANDS else (29, 30, 31)
    work_areas: list[ProjectedWorkArea] = []
    for zone in supported_zones:
        zone_west, zone_east = _ZONE_LONGITUDE_RANGES[zone]
        part_west = max(bounds.west, zone_west)
        part_east = min(bounds.east, zone_east)
        if part_east <= part_west:
            continue
        epsg = 4083 if zone == 28 else 25800 + zone
        work_areas.append(
            ProjectedWorkArea(
                BBoxWGS84(part_west, bounds.south, part_east, bounds.north),
                crs_from_epsg(epsg),
            )
        )
    if not work_areas:
        raise NoCoverageError("ROI does not intersect a supported Spanish UTM zone")
    return tuple(work_areas)
