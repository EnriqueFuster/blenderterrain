"""Plan projected texture requests for PNOA imagery."""

from ..core.imagery import ImageryTileRequest, plan_texture_tiles
from ..core.planning import ImportPlan


def plan_pnoa_tiles(plan: ImportPlan) -> tuple[ImageryTileRequest, ...]:
    """Build deterministic PNOA texture requests."""

    return plan_texture_tiles(plan, "pnoa")
