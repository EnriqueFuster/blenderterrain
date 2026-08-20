"""Project-specific errors for portable BlenderTerrain modules."""


class BlenderTerrainError(Exception):
    """Base class for expected project failures."""


class UserInputError(BlenderTerrainError):
    """Raised when user-provided geometry or options are invalid."""


class NoCoverageError(BlenderTerrainError):
    """Raised when an area does not intersect supported geographic coverage."""


class PlanningLimitExceeded(UserInputError):
    """Raised when requested output exceeds a hard MVP safety budget."""


class ProviderUnavailableError(BlenderTerrainError):
    """Raised when a provider cannot complete a request."""


class ProviderContractChanged(BlenderTerrainError):
    """Raised when an external service no longer satisfies its observed contract."""


class CatalogContractChanged(ProviderContractChanged):
    """Raised when provider HTML no longer satisfies the observed contract."""


class DownloadIntegrityError(BlenderTerrainError):
    """Raised when a downloaded resource fails safety or format validation."""


class DownloadAuthorizationRequired(BlenderTerrainError):
    """Raised when a provider requires an interactive license confirmation."""


class RasterFormatError(BlenderTerrainError):
    """Raised when a raster uses a TIFF layout unsupported by the local reader."""
