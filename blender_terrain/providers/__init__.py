"""External data-provider adapters."""

from .copernicus_dem import Glo30Tile, glo30_tiles_for_roi

__all__ = ["Glo30Tile", "glo30_tiles_for_roi"]
