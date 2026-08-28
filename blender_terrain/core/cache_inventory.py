"""Read-only, bounded inventory of BlenderTerrain-owned cache areas."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..errors import UserInputError

CACHE_CATEGORIES = ("elevation", "imagery", "processed", "jobs")
DEFAULT_MAXIMUM_ENTRIES = 1_000_000


@dataclass(frozen=True, slots=True)
class CacheCategoryInventory:
    """Space and file counts for one extension-owned cache category."""

    name: str
    file_count: int
    byte_count: int
    partial_file_count: int


@dataclass(frozen=True, slots=True)
class CacheInventory:
    """Aggregate cache information suitable for UI display and policy checks."""

    root: Path
    categories: tuple[CacheCategoryInventory, ...]

    @property
    def file_count(self) -> int:
        return sum(category.file_count for category in self.categories)

    @property
    def byte_count(self) -> int:
        return sum(category.byte_count for category in self.categories)

    @property
    def partial_file_count(self) -> int:
        return sum(category.partial_file_count for category in self.categories)


@dataclass(frozen=True, slots=True)
class CacheRemovalResult:
    """Files and bytes removed by one confirmed cache maintenance action."""

    file_count: int
    byte_count: int


def inspect_cache(
    root: Path, maximum_entries: int = DEFAULT_MAXIMUM_ENTRIES
) -> CacheInventory:
    """Inspect known cache directories without following symbolic links."""

    if maximum_entries <= 0:
        raise ValueError("Maximum cache entries must be positive")
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise UserInputError("The configured cache directory does not exist")
    remaining = maximum_entries
    categories: list[CacheCategoryInventory] = []
    for name in CACHE_CATEGORIES:
        category, visited = _inspect_category(resolved_root, name, remaining)
        remaining -= visited
        categories.append(category)
    return CacheInventory(resolved_root, tuple(categories))


def clear_cache(root: Path, selection: str) -> CacheRemovalResult:
    """Remove a known category or incomplete files from a validated cache root."""

    resolved_root = root.expanduser().resolve()
    if resolved_root == Path(resolved_root.anchor) or resolved_root == Path.home().resolve():
        raise UserInputError("Refusing to clean a broad filesystem or home directory")
    if not resolved_root.is_dir():
        raise UserInputError("The configured cache directory does not exist")
    if selection == "PARTIALS":
        return _clear_partial_files(resolved_root)
    names = CACHE_CATEGORIES if selection == "ALL" else (selection,)
    if any(name not in CACHE_CATEGORIES for name in names):
        raise UserInputError("Unknown cache cleanup category")
    removed_files = 0
    removed_bytes = 0
    for name in names:
        directory = resolved_root / name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise UserInputError(f"Cache category is not a regular directory: {name}")
        inventory, _visited = _inspect_category(resolved_root, name, DEFAULT_MAXIMUM_ENTRIES)
        removed_files += inventory.file_count
        removed_bytes += inventory.byte_count
        for child in tuple(directory.iterdir()):
            if child.is_symlink():
                raise UserInputError(f"Cache category contains a symbolic link: {name}")
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
            except OSError as exc:
                raise UserInputError(f"Cannot remove cached item: {child.name}") from exc
    return CacheRemovalResult(removed_files, removed_bytes)


def _clear_partial_files(root: Path) -> CacheRemovalResult:
    removed_files = 0
    removed_bytes = 0
    for name in CACHE_CATEGORIES:
        directory = root / name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise UserInputError(f"Cache category is not a regular directory: {name}")
        pending = [directory]
        while pending:
            current = pending.pop()
            for entry in tuple(os.scandir(current)):
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".part"):
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                        Path(entry.path).unlink()
                    except OSError as exc:
                        raise UserInputError(
                            f"Cannot remove incomplete cache file: {entry.name}"
                        ) from exc
                    removed_files += 1
                    removed_bytes += size
    return CacheRemovalResult(removed_files, removed_bytes)


def _inspect_category(
    root: Path, name: str, maximum_entries: int
) -> tuple[CacheCategoryInventory, int]:
    directory = root / name
    if not directory.exists():
        return CacheCategoryInventory(name, 0, 0, 0), 0
    if directory.is_symlink() or not directory.is_dir():
        raise UserInputError(f"Cache category is not a regular directory: {name}")
    files = 0
    bytes_ = 0
    partials = 0
    visited = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = tuple(os.scandir(current))
        except OSError as exc:
            raise UserInputError(f"Cannot inspect cache category: {name}") from exc
        for entry in entries:
            visited += 1
            if visited > maximum_entries:
                raise UserInputError("Cache inventory exceeds the configured entry limit")
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            try:
                bytes_ += entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                raise UserInputError(f"Cannot inspect cache file: {entry.name}") from exc
            files += 1
            if entry.name.endswith(".part"):
                partials += 1
    return CacheCategoryInventory(name, files, bytes_, partials), visited
