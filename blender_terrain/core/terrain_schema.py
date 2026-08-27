"""Versioned metadata for terrain collections and objects stored in Blender."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..errors import RasterFormatError

TERRAIN_SCHEMA_VERSION = 2
MAX_SUBDIVISION_LEVEL = 11
SUBDIVISION_WARNING_LEVEL = 3


class TerrainRepresentation(StrEnum):
    """How elevation is represented by one imported terrain."""

    BAKED = "BAKED"
    DISPLACEMENT = "DISPLACEMENT"


@dataclass(frozen=True, slots=True)
class TerrainSettings:
    """Editable settings shared by one terrain import."""

    vertical_scale: float = 1.0
    displacement_midlevel: float = 0.0
    subdivision_viewport: int = 0
    subdivision_render: int = 0
    displacement_enabled: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.vertical_scale) or self.vertical_scale <= 0.0:
            raise ValueError("Terrain vertical scale must be finite and positive")
        if not math.isfinite(self.displacement_midlevel) or not (
            0.0 <= self.displacement_midlevel <= 1.0
        ):
            raise ValueError("Terrain displacement midlevel must be between zero and one")
        if not 0 <= self.subdivision_viewport <= MAX_SUBDIVISION_LEVEL:
            raise ValueError("Viewport subdivision exceeds Blender's supported range")
        if not 0 <= self.subdivision_render <= MAX_SUBDIVISION_LEVEL:
            raise ValueError("Render subdivision exceeds Blender's supported range")


@dataclass(frozen=True, slots=True)
class TerrainMetadata:
    """Minimum compatible metadata recovered from a Blender data block."""

    schema_version: int
    representation: TerrainRepresentation
    settings: TerrainSettings
    legacy: bool = False


def subdivision_risk_message(viewport: int, render: int) -> str | None:
    """Describe exponential subdivision cost when either level is potentially unsafe."""

    highest = max(viewport, render)
    if highest < SUBDIVISION_WARNING_LEVEL:
        return None
    multiplier = 4**highest
    return (
        f"Subdivision level {highest} can generate up to {multiplier:,} faces per "
        "base face; Blender may become unresponsive or run out of memory"
    )


def read_terrain_metadata(properties: Mapping[str, object]) -> TerrainMetadata:
    """Read current metadata or interpret an unversioned 0.1 terrain as baked."""

    raw_version = properties.get("blender_terrain_schema_version")
    if raw_version is None:
        return TerrainMetadata(
            1,
            TerrainRepresentation.BAKED,
            TerrainSettings(
                vertical_scale=_positive_float(
                    properties, "blender_terrain_vertical_scale", 1.0
                )
            ),
            legacy=True,
        )
    try:
        if isinstance(raw_version, bool) or not isinstance(raw_version, int):
            raise TypeError
        version = raw_version
        if version != TERRAIN_SCHEMA_VERSION:
            raise ValueError
        raw_representation = properties["blender_terrain_representation"]
        if not isinstance(raw_representation, str):
            raise TypeError
        representation = TerrainRepresentation(raw_representation)
        settings = TerrainSettings(
            vertical_scale=_positive_float(
                properties, "blender_terrain_vertical_scale", 1.0
            ),
            displacement_midlevel=_bounded_float(
                properties, "blender_terrain_displacement_midlevel", 0.0, 0.0, 1.0
            ),
            subdivision_viewport=_integer(
                properties, "blender_terrain_subdivision_viewport", 0
            ),
            subdivision_render=_integer(
                properties, "blender_terrain_subdivision_render", 0
            ),
            displacement_enabled=_boolean(
                properties, "blender_terrain_displacement_enabled", True
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RasterFormatError("Terrain contains unsupported or invalid metadata") from exc
    return TerrainMetadata(version, representation, settings)


def _positive_float(properties: Mapping[str, object], key: str, default: float) -> float:
    value = properties.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0.0:
        raise ValueError
    return converted


def _integer(properties: Mapping[str, object], key: str, default: int) -> int:
    value = properties.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError
    return value


def _bounded_float(
    properties: Mapping[str, object], key: str, default: float, minimum: float, maximum: float
) -> float:
    value = properties.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError
    converted = float(value)
    if not math.isfinite(converted) or not minimum <= converted <= maximum:
        raise ValueError
    return converted


def _boolean(properties: Mapping[str, object], key: str, default: bool) -> bool:
    value = properties.get(key, default)
    if not isinstance(value, bool):
        raise ValueError
    return value
