from __future__ import annotations

from pathlib import Path

import pytest

from blender_terrain.core.cache_inventory import clear_cache, inspect_cache
from blender_terrain.errors import UserInputError


def test_inspects_only_owned_cache_categories(tmp_path: Path) -> None:
    (tmp_path / "elevation").mkdir()
    (tmp_path / "elevation" / "source.tif").write_bytes(b"1234")
    (tmp_path / "imagery" / "request").mkdir(parents=True)
    (tmp_path / "imagery" / "request" / "tile.png.part").write_bytes(b"12")
    (tmp_path / "unrelated.txt").write_bytes(b"ignored")

    inventory = inspect_cache(tmp_path)

    by_name = {category.name: category for category in inventory.categories}
    assert inventory.file_count == 2
    assert inventory.byte_count == 6
    assert inventory.partial_file_count == 1
    assert by_name["elevation"].file_count == 1
    assert by_name["imagery"].partial_file_count == 1
    assert by_name["processed"].byte_count == 0


def test_rejects_a_non_directory_root(tmp_path: Path) -> None:
    path = tmp_path / "cache.txt"
    path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(UserInputError, match="does not exist"):
        inspect_cache(path)


def test_enforces_bounded_inventory(tmp_path: Path) -> None:
    directory = tmp_path / "jobs"
    directory.mkdir()
    (directory / "one.json").write_text("{}", encoding="utf-8")
    (directory / "two.json").write_text("{}", encoding="utf-8")

    with pytest.raises(UserInputError, match="entry limit"):
        inspect_cache(tmp_path, maximum_entries=1)


def test_clears_only_selected_cache_category(tmp_path: Path) -> None:
    (tmp_path / "elevation").mkdir()
    (tmp_path / "imagery").mkdir()
    (tmp_path / "elevation" / "source.tif").write_bytes(b"1234")
    (tmp_path / "imagery" / "texture.png").write_bytes(b"keep")

    result = clear_cache(tmp_path, "elevation")

    assert result.file_count == 1
    assert result.byte_count == 4
    assert not (tmp_path / "elevation" / "source.tif").exists()
    assert (tmp_path / "imagery" / "texture.png").is_file()


def test_clears_partial_files_without_removing_complete_data(tmp_path: Path) -> None:
    directory = tmp_path / "jobs" / "one"
    directory.mkdir(parents=True)
    (directory / "result.json").write_bytes(b"complete")
    (directory / "result.json.part").write_bytes(b"partial")

    result = clear_cache(tmp_path, "PARTIALS")

    assert result.file_count == 1
    assert (directory / "result.json").is_file()
    assert not (directory / "result.json.part").exists()


def test_refuses_to_clean_home_directory() -> None:
    with pytest.raises(UserInputError, match="broad"):
        clear_cache(Path.home(), "ALL")
