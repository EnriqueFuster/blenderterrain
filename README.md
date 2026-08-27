# BlenderTerrain

BlenderTerrain is an independent, from-scratch Blender terrain importer for
Spain's official IGN/CNIG MDT02, MDS02, and PNOA services.

The repository name is intentionally broader than its initial geographic
coverage. Version 1 will remain focused on Spain; support for another country
will require an actual provider and compatible data rather than speculative
abstractions.

## Current status

The Blender 4.5 extension implements an end-to-end workflow: define a WGS84
bounding box directly or from a centre and metric dimensions, choose MDT02 or
MDS02 and an output resolution, optionally request PNOA imagery, discover and
download the official sources, process bounded terrain tiles, and create
georeferenced Blender mesh objects and materials. Network and raster work runs
in a background Blender process with observable states and cooperative
cancellation.

The current implementation includes:

1. paginated MDT02 and MDS02 discovery without browser automation;
2. validated, atomic CNIG TIFF downloads and cache reuse;
3. bounded BigTIFF reading, mosaicking, NoData handling and bilinear resampling;
4. deterministic terrain tiling with identical shared-edge elevations;
5. PNOA WMS textures in the supported Spanish ETRS89 UTM zones;
6. local Blender coordinates with CRS, ROI, source and attribution metadata;
7. automatic or explicit row-by-column terrain division;
8. full-resolution persistent heightmaps with a lightweight progressive base mesh
   or an opt-in full-resolution base mesh;
9. import-wide and per-object vertical scale and subdivision controls;
10. optional packing of PNOA images into the `.blend` file.

Subdivision follows Blender's technical range from 0 to 11. The interface warns
from level 3 because each additional level can multiply the generated face count
by four; high values can freeze Blender or exhaust system and GPU memory.

The first MVP workflow was validated end to end from an isolated Blender 4.5.3
extension installation on 2026-08-26 with both MDT02 and MDS02, including live
PNOA imagery, terrain processing and Blender object creation.

Current limitations are deliberate: there is no interactive map or complex
polygon ROI yet, terrain creation is synchronous, and a large number of PNOA
tiles has not yet been GPU-benchmarked. The default progressive mesh approaches
the processed heightmap density around subdivision level 4; higher levels only
interpolate the available raster samples. Per-object displacement overrides can
also create visible seams when adjacent objects use different strengths; the
extension warns when it detects that situation.

No elevation or imagery data is redistributed in this repository.

## Architecture direction

Portable geospatial logic must not import `bpy`. Blender integration will be a
consumer of tested artifacts produced by the portable layers. Provider-specific
HTTP and parsing behavior will remain isolated from core geospatial models.

See the proposed decisions in [`docs/adr`](docs/adr) and the verified-source
inventory in [`docs/data-sources.md`](docs/data-sources.md).

## Install in Blender

Build the extension ZIP with Blender 4.5:

```text
blender --command extension build --output-dir .artifacts/extension-build
```

Install the resulting ZIP from Blender through **Edit > Preferences > Get
Extensions > Install from Disk**. Enable Blender online access, select a cache
directory in the extension preferences, and open **3D View > Sidebar > Terrain**.
Run `Validate ROI`, `Discover Sources`, `Download Data`, and `Create Terrain` in
that order. A compact manual feedback test is available in
[`docs/manual-testing.md`](docs/manual-testing.md).

## Development

The portable checks require Python 3.11 or later:

```text
python -m unittest discover -s tests -v
```

Optional development tools are declared in `pyproject.toml`. Online tests will
always be opt-in and must never perform large downloads by default.

The read-only catalog diagnostic is explicitly enabled with:

```text
python -m scripts.discover_cnig --product MDT02 --online
python -m scripts.discover_cnig --product MDS02 --online
python -m scripts.discover_cnig --product PNOA_MA --online
```

An explicit `--download-one` mode exists for controlled provider validation.
Complete MDT02 and MDS02 samples were downloaded and validated on 2026-08-19
through the first-party CNIG initialization and delivery endpoints. See
`docs/provider-cnig.md` for the observed contract and current limitations.

PNOA results can contain several revisions with the same filename. A research
download must therefore select the exact identifier printed by discovery:

```text
python -m scripts.discover_cnig --product PNOA_MA --online --sequential-id 12570809 --download-directory .artifacts/pnoa-sample
```

That command downloads a large source image and is never run by the test suite.
A complete 2024 Valencia PNOA sample was downloaded and validated on 2026-08-20.

The opt-in WMS check fetches current capabilities and can download a 512 by 512
PNG control image:

```text
python -m scripts.check_pnoa_wms --online --download-directory .artifacts/pnoa-wms-control
```

The control image was compared with the corresponding pixels in the downloaded
PNOA source and matched exactly, including orientation.

Elevation bounds are expanded to outer pixel edges so an ROI never loses its
border pixels. Mosaic output is limited to 16777216 pixels by default and keeps
a per-pixel source index. Overlapping valid elevation values use explicit source
order and produce conflict counts and a maximum difference instead of silently
hiding provider seams.

## License

Code is licensed under GPL-3.0-or-later. Official geographic data is not part of
the code license and retains the terms published by its provider.
