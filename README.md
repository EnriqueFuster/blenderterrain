# BlenderTerrain

BlenderTerrain is a Blender extension for acquiring geospatial elevation and imagery and turning
them into editable, georeferenced terrain. Define an area, choose the data products, prepare them
in the background, and create a tiled terrain with displacement, materials and provenance.

It is designed for artists and technical users who need reliable geographic terrain without
manually discovering map sheets, reconciling coordinate systems or preparing displacement
textures outside Blender.

> **Demo video / GIF placeholder**
>
> A short end-to-end workflow demonstration will be added here.

## Highlights

- Interactive browser map, bounding-box, centre-and-size and polygon ROI inputs.
- Official high-resolution terrain and orthophotography for Spain and metropolitan France.
- Worldwide elevation, surface, satellite-composite and seabed fallback products.
- Independent DTM, DSM, imagery and bathymetry choices where coverage overlaps.
- Background acquisition with progress, cancellation, retry and validated cache reuse.
- Automatic CRS handling, bounded memory planning, mosaicking and deterministic terrain tiling.
- Real-world scale: one Blender unit represents one metre.
- Editable displacement, subdivision, midlevel, smoothing and per-object overrides.
- Optional seabed composition, image packing, GeoTIFF export and bake-and-merge.
- Local elevation GeoTIFF and georeferenced local imagery workflows.

## Data coverage

BlenderTerrain displays products compatible with the selected ROI. National products and global
alternatives can appear together; the user confirms the source for each layer. A provider failure
never triggers an unconfirmed fallback.

| Area | Elevation | Imagery | Nominal source resolution |
| --- | --- | --- | --- |
| Spain | IGN/CNIG MDT and MDS families | PNOA Máxima Actualidad | 0.5–200 m elevation; PNOA varies spatially, catalogued at 0.25 m |
| Metropolitan France and Corsica | IGN France RGE ALTI and MNS-Correl | IGN France BD ORTHO | 1 m DTM, 0.5 m DSM, 0.2 m orthophoto |
| Worldwide fallback | GEDTM30 modelled DTM; Copernicus DEM GLO-30 DSM | ESA WorldCover Sentinel-2 composite 2021 | 30 m elevation; 10 m imagery composite |
| Global ocean | GEBCO grid | Seabed material composed with available imagery | 15 arc-seconds, about 463 m at the equator |

These values are product grid spacings, not guaranteed positional or vertical accuracy. Coverage
can be incomplete inside broad envelopes and is checked during discovery or acquisition.
GEDTM30 is a **modelled DTM**, Copernicus GLO-30 is a **DSM**, and WorldCover is a static 2021
composite rather than current orthophotography. GEBCO is not intended for navigation and does not
provide reliable inland-water bathymetry.

See [Data providers and products](docs/README.md) for detailed semantics, licensing,
limitations, endpoints and researched future sources.

## Installation

BlenderTerrain requires Blender 4.5 or later.

1. Download the BlenderTerrain extension ZIP from the project releases.
2. Open **Edit > Preferences > Get Extensions** in Blender.
3. Choose **Install from Disk** and select the ZIP without extracting it.
4. Allow online access and configure a cache directory in the extension preferences.
5. Open **3D View > Sidebar > Terrain**.

To build the ZIP from source:

```text
blender --command extension build --output-dir .artifacts/extension-build
```

## Basic workflow

1. Choose **Download Official Data** or **Use Local Rasters**.
2. For online data, define and validate an area of interest.
3. Choose elevation, imagery, resolution, resource profile, terrain division and marine mode.
4. Discover the sources, review the selection, then download and prepare the data.
5. Create the terrain and adjust displacement and subdivision in **Imported Terrain**.
6. Optionally export prepared GeoTIFFs, pack imagery, or bake and merge the terrain.

Start with a small ROI and the **Balanced** resource profile. Native high-resolution data over a
large area can require millions of samples and substantial cache, RAM and GPU memory. Preflight
rejects requests beyond the selected safety profile.

## Terrain output

The default result is a collection of adjacent terrain objects using the same spatial grid for
elevation and imagery. Tiling keeps meshes manageable while preserving matching shared edges.
Each object retains the information needed to reconstruct its displacement and texture placement.

The initial base mesh is intentionally light. Increase viewport or render subdivision to reveal
more of the prepared heightmap; levels beyond the source-grid requirement only interpolate
existing samples. **Bake and Merge Terrain** applies evaluated geometry, preserves material
assignments and removes the editable source tiles after confirmation.

## Local data

Local elevation input accepts the constrained tiled Float32 GeoTIFF layouts validated by the
project. Files must share a compatible CRS and pixel grid and collectively cover the requested
area. Local imagery uses PNG with matching PGW/WLD and PRJ sidecars and must overlap elevation.
Unsupported layouts fail explicitly rather than being interpreted silently.

## Known limitations

- Exact national coverage and acquisition dates vary within published envelopes.
- Very large native-resolution requests remain bounded to protect Blender and the workstation.
- Large imagery mosaics have not been comprehensively benchmarked across GPUs.
- Terrain object creation runs in Blender's main process after background preparation finishes.
- Different displacement settings on neighbouring objects can create visible seams.
- Dynamic Sentinel-2 acquisition and inland-water bathymetry are not implemented.

## Development

Portable geospatial modules do not import `bpy`; Blender-specific code is confined to the addon
and UI boundary. Provider HTTP contracts are isolated from core planning and raster logic.

```text
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check blender_terrain tests
python -m mypy blender_terrain
```

Run Blender validation with a factory startup so third-party addons cannot affect it:

```text
blender --background --factory-startup --python scripts/smoke_test_blender.py -- /path/to/blenderterrain
```

Online diagnostics are opt-in and are never part of the default test suite.

## License and data attribution

BlenderTerrain code is licensed under GPL-3.0-or-later. Geographic data is not redistributed and
remains subject to each provider's license and attribution requirements. Generated terrain stores
source metadata, but users remain responsible for the applicable terms when publishing results.

## Gallery

> **Render example 1 placeholder** — high-resolution national terrain and orthophotography.

> **Render example 2 placeholder** — coastal terrain with composed bathymetry.

> **Render example 3 placeholder** — worldwide fallback terrain.
