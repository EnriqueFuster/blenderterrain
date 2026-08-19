"""Project-specific errors for portable BlenderTerrain modules."""


class BlenderTerrainError(Exception):
    """Base class for expected project failures."""


class ProviderUnavailableError(BlenderTerrainError):
    """Raised when a provider cannot complete a request."""


class CatalogContractChanged(BlenderTerrainError):
    """Raised when provider HTML no longer satisfies the observed contract."""
