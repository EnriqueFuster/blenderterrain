# BlenderTerrain project context

All commands below run from the repository root:
`C:\Users\Usuario\code\blenderterrain`.

## Identity and supported environment

- Source package: `blender_terrain`; Blender extension entry point: root `__init__.py`.
- Extension identity: `id = "blender_terrain"`, name `BlenderTerrain`, version `0.5.0`,
  type `add-on`, GPL-3.0-or-later (`blender_manifest.toml`).
- Supported Blender: `4.5.0` or later. No maximum version or CI version matrix is
  declared; `.github/` is absent.
- Python package: Python `>=3.11` (`pyproject.toml`). Bundled PyProj wheels target
  CPython 3.11, 3.12 and 3.13 on the platforms declared in the manifest. This is
  packaging coverage, not a wider Blender support declaration.
- Installed runtime verified with the bundled environment report: Blender `5.2.0 LTS`,
  Python `3.13.13`, Windows AMD64, background mode. Blender is not on `PATH`; the
  executable is `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`.
- Development virtual environment verified at `.venv`: Python `3.13.7`, pytest
  `9.1.1`, Ruff `0.16.3`, and mypy `2.3.1`.

## Architecture and conventions

- Portable code remains importable without Blender. `bpy` imports are confined to
  `blender_terrain/addon.py`, `blender_terrain/ui/`, and Blender smoke scripts.
- Provider-neutral acquisition uses `core.acquisition.RasterAcquirer.acquire(...)`
  and returns `AcquiredRasterLayer`. Provider adapters are registered in
  `providers/registry.py`; implementations are grouped under `spain/`, `france/`,
  `uk/`, and `worldwide/`.
- ROI coordinates are WGS84 longitude/latitude in that order. PyProj transformers use
  `always_xy=True`; projected coordinates are easting/northing and horizontal axes must
  be metre-based. Processing grids are north-up, pixel-is-area, globally aligned to the
  selected resolution, and tiled to at most `512 x 512` cells by default.
- One Blender unit represents one metre. Each EPSG group uses the minimum tile west and
  south as its local scene origin; terrain objects store their EPSG and absolute bounds.
- NoData is preserved through raster readers and processing. Terrain faces touching
  NoData nodes are omitted, and unused vertices are removed. Elevation mosaics require
  a common EPSG, pixel resolution, NoData value, and pixel-grid alignment.
- Resource profiles `(elevation samples, imagery pixels)` are: Conservative
  `(4,194,304, 16,777,216)`, Balanced `(16,777,216, 67,108,864)`, and Large
  `(67,108,864, 268,435,456)`. Source elevation windows default to `4,194,304`
  pixels. Cache paths are user-selected in extension preferences; persisted jobs live
  below `<cache>/jobs/<task-id>` and downloads use validated, bounded destinations.
- Long-running acquisition uses persisted jobs and a separate Blender worker process;
  Blender data creation and other `bpy` access remain on the main process/thread.

## Validation commands

Python checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check blender_terrain tests scripts
.\.venv\Scripts\mypy.exe blender_terrain
```

Disposable installed-runtime report:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python-exit-code 1 --python .agents/skills/blender-addon-development/scripts/env_report.py
```

Blender registration, operators, terrain creation and cleanup smoke test:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --background --factory-startup --python-exit-code 1 --python scripts/smoke_test_blender.py -- (Resolve-Path .).Path
```

Build the Blender extension ZIP (the output directory must already exist):

```powershell
New-Item -ItemType Directory -Force .artifacts/extension-build | Out-Null
& 'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe' --factory-startup --command extension build --output-dir .artifacts/extension-build
```

Build the Python wheel using the existing development environment:

```powershell
New-Item -ItemType Directory -Force .artifacts/wheel-build | Out-Null
.\.venv\Scripts\python.exe -m build --wheel --no-isolation --outdir .artifacts/wheel-build
```

The default pytest suite is offline. Scripts named `probe_*` and provider pipeline
smokes may contact external services and require explicit cache/output arguments; they
are not default validation commands.

## Packaging and manual checks

- The Blender extension ZIP is built directly from the repository using
  `blender_manifest.toml`; it bundles platform/ABI-specific PyProj wheels and the
  OSTN15 grid. It must not contain the project wheel.
- The `blenderterrain` wheel is built from the same `blender_terrain` package through
  Setuptools (`pyproject.toml`). Runtime `pip install` from Blender is not supported.
- Manual UI coverage remains necessary for interactive map/browser behavior, sidebar
  layout, modal interaction, viewport appearance, texture quality and visual seams.
  The documented workflow is: install ZIP from disk, enable online access, select a
  cache directory, open `3D View > Sidebar > Terrain`, validate a small ROI, discover,
  prepare, and create terrain.
- CI validation is pending because no repository CI configuration currently exists.
