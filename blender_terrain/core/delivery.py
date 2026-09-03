"""Provider-neutral transfer progress contract."""

from __future__ import annotations

from dataclasses import dataclass


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


__all__ = ["TransferProgress"]
