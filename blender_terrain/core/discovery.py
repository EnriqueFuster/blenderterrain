"""Compatibility imports for the former core CNIG discovery API."""

from ..providers.cnig_discovery import (
    CatalogDiscoveryProvider,
    DiscoveryResult,
    discover_sources,
    select_catalog_items,
)

__all__ = [
    "CatalogDiscoveryProvider",
    "DiscoveryResult",
    "discover_sources",
    "select_catalog_items",
]
