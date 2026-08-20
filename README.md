# BlenderTerrain

BlenderTerrain is an independent, from-scratch Blender terrain importer under
active technical validation. Its first supported data sources will be Spain's
official IGN/CNIG MDT02, MDS02, and PNOA services.

The repository name is intentionally broader than its initial geographic
coverage. Version 1 will remain focused on Spain; support for another country
will require an actual provider and compatible data rather than speculative
abstractions.

## Current status

The portable technical validation outside Blender is complete. The repository
also contains an installable Blender 4.5 extension skeleton with a sidebar
panel and a configurable cache directory. No production import workflow is
available yet.

The current foundation has verified:

1. reproducible MDT02, MDS02, and PNOA catalog discovery without browser automation;
2. complete first-party CNIG downloads for all three products;
3. bounded elevation-window reading plus CRS, transform, and bounds parsing;
4. exact numerical and spatial agreement with Rasterio as a development oracle;
5. projected PNOA WMS texture downloads with validated dimensions and orientation;
6. bounded elevation mosaics across aligned CNIG source files.

The raster reader matched Rasterio exactly for valid Float32 values and NoData.
The projected WMS control image matched its source orthophoto pixel for pixel.
Both required architecture decisions are accepted, and a live bounded-download
test confirmed that failed CNIG transfers leave no partial file behind.

The WMS is the intended default for ROI textures because a single PNOA MTN25
source image can approach 1 GB. The current WMS implementation is deliberately
limited to the control-tested EPSG:25830 axis order.

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
Extensions > Install from Disk**. The empty **Terrain** panel appears in the 3D
View sidebar. This is currently an integration shell, not a usable terrain
importer.

## Development

The portable checks require Python 3.11 or later:

```text
python -m unittest discover -s tests -v
```

Optional development tools are declared in `pyproject.toml`. Online tests will
always be opt-in and must never perform large downloads by default.

The Phase 0 read-only catalog experiment is explicitly enabled with:

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
