"""Provider-neutral transfer progress and delivery result contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class TransferProgress:
    """Byte progress for one file within a multi-file delivery."""

    kind: str
    file_index: int
    file_count: int
    filename: str
    written_bytes: int
    expected_bytes: int | None
    cached: bool = False


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Validated cache paths produced or reused by one import request."""

    elevation_paths: tuple[Path, ...]
    imagery_paths: tuple[Path, ...]
    warnings: tuple[str, ...] = ()
    cached_elevation_count: int = 0
    cached_imagery_count: int = 0


def deliver_plan_sources(*args: Any, **kwargs: Any) -> DeliveryResult:
    """Call the former Spanish delivery API retained for compatibility."""

    from ..providers.cnig_delivery import deliver_plan_sources as deliver

    return deliver(*args, **kwargs)


__all__ = ["DeliveryResult", "TransferProgress", "deliver_plan_sources"]
