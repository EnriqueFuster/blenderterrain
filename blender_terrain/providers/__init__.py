"""External data-provider adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .copernicus_dem import Glo30Tile

__all__ = ["Glo30Tile", "glo30_tiles_for_roi"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from . import copernicus_dem

        return getattr(copernicus_dem, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
