"""Project-specific errors for portable BlenderTerrain modules."""


class BlenderTerrainError(Exception):
    """Base class for expected project failures."""


class ProviderUnavailableError(BlenderTerrainError):
    """Raised when a provider cannot complete a request."""


class CatalogContractChanged(BlenderTerrainError):
    """Raised when provider HTML no longer satisfies the observed contract."""


class DownloadIntegrityError(BlenderTerrainError):
    """Raised when a downloaded resource fails safety or format validation."""


class DownloadAuthorizationRequired(BlenderTerrainError):
    """Raised when a provider requires an interactive license confirmation."""
