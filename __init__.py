"""Blender Extension entry point for BlenderTerrain."""

from __future__ import annotations


def register() -> None:
    """Register BlenderTerrain classes with Blender."""

    from .blender_terrain import addon

    addon.register(__package__)


def unregister() -> None:
    """Unregister every BlenderTerrain class from Blender."""

    from .blender_terrain import addon

    addon.unregister()
