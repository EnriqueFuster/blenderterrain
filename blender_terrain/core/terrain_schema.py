"""Versioned metadata for terrain collections and objects stored in Blender."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ..errors import RasterFormatError

TERRAIN_SCHEMA_VERSION = 2


class TerrainRepresentation(StrEnum):
    """How elevation is represented by one imported terrain."""

    BAKED = "BAKED"
    DISPLACEMENT = "DISPLACEMENT"


@dataclass(frozen=True, slots=True)
class TerrainSettings:
    """Editable settings shared by one terrain import."""

    vertical_scale: float = 1.0
    subdivision_viewport: int = 0
    subdivision_render: int = 0
    displacement_enabled: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.vertical_scale) or self.vertical_scale <= 0.0:
            raise ValueError("Terrain vertical scale must be finite and positive")
        if not 0 <= self.subdivision_viewport <= 6:
            raise ValueError("Viewport subdivision must be between zero and six")
        if not 0 <= self.subdivision_render <= 8:
            raise ValueError("Render subdivision must be between zero and eight")


@dataclass(frozen=True, slots=True)
class TerrainMetadata:
    """Minimum compatible metadata recovered from a Blender data block."""

    schema_version: int
    representation: TerrainRepresentation
    settings: TerrainSettings
    legacy: bool = False


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


def _boolean(properties: Mapping[str, object], key: str, default: bool) -> bool:
    value = properties.get(key, default)
    if not isinstance(value, bool):
        raise ValueError
    return value
