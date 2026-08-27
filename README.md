# BlenderTerrain

BlenderTerrain is an independent, from-scratch Blender terrain importer for
Spain's official IGN/CNIG MDT, MDS, and PNOA services.

The repository name is intentionally broader than its initial geographic
coverage. Version 1 will remain focused on Spain; support for another country
will require an actual provider and compatible data rather than speculative
abstractions.

## Current status

The Blender 4.5 extension implements an end-to-end workflow: define a WGS84
bounding box directly or from a centre and metric dimensions, or load Polygon
and MultiPolygon geometry from GeoJSON, KML, Shapefile, or a selected GeoPackage
polygon layer, or draw a rectangle or polygon on a browser map; choose a supported
MDT or MDS coverage and an output resolution; optionally request PNOA imagery; and create
georeferenced Blender mesh objects and materials. Network and raster work runs
in a background Blender process with observable states and cooperative
cancellation. Shapefiles require their matching `.prj`; supported input CRSs
are WGS84, ETRS89, REGCAN95 and their common Spanish UTM zones. GeoPackage files
are opened read-only and require an explicit Polygon or MultiPolygon layer selection.

The current implementation includes:

1. paginated discovery for MDT50 cm, MDT02, MDT05, MDT25, MDT200, MDS50 cm,
   MDS02 and MDS05 without browser automation;
2. validated, atomic CNIG TIFF downloads and cache reuse;
3. bounded BigTIFF reading, mosaicking, NoData handling and bilinear resampling;
4. deterministic terrain tiling with identical shared-edge elevations;
5. PNOA WMS textures in the supported Spanish ETRS89 UTM zones;
6. local Blender coordinates with CRS, ROI, source and attribution metadata;
7. automatic or explicit row-by-column terrain division;
8. full-resolution persistent heightmaps with a lightweight progressive base mesh
   or an opt-in full-resolution base mesh;
9. import-wide and per-object vertical scale, displacement strength, Midlevel and
   subdivision controls;
10. optional packing of PNOA images into the `.blend` file;
11. compatible local elevation TIFF or TIFF-folder processing without copying the
    source files;
12. optional automatic 3D viewport `Clip End` adjustment after creation.
13. conservative, balanced and large resource profiles that change elevation and
    imagery planning limits before any download starts;
14. cache inspection and selective cleanup for elevation, imagery, processed
    terrain, job records, or incomplete files;
15. retry of the last interrupted or failed acquisition job, reusing every
    validated cached file; and
16. per-stage delivery timing and an explicit cached-file reuse summary.

Subdivision follows Blender's technical range from 0 to 11. The interface warns
from level 3 because each additional level can multiply the generated face count
by four; high values can freeze Blender or exhaust system and GPU memory.

The first MVP workflow was validated end to end from an isolated Blender 4.5.3
extension installation on 2026-08-26 with both MDT02 and MDS02, including live
PNOA imagery, terrain processing and Blender object creation.

The browser selector communicates with Blender through a temporary token-protected
server bound only to `127.0.0.1`. PNOA Máxima Actualidad is its default aerial
imagery background; IGN physical relief, IGN topographic mapping and OpenStreetMap streets
can be selected without changing the resulting ROI. The ROI is not uploaded by
BlenderTerrain; internet access is used only to obtain the visible, attributed map
tiles. The local server closes after confirmation, cancellation, or extension shutdown.

Current limitations are deliberate: terrain creation is synchronous, and a large
number of PNOA tiles has not yet been GPU-benchmarked. The default progressive mesh approaches
the processed heightmap density around subdivision level 4; higher levels only
interpolate the available raster samples. Per-object displacement overrides can
also create visible seams when adjacent objects use different strengths; the
extension warns when it detects that situation.
The local-raster mode accepts the constrained CNIG Float32 tiled BigTIFF layouts
verified by this project, including TIFF horizontal differencing. Other GeoTIFF
layouts fail explicitly; broad arbitrary-raster support would require shipping a
larger GDAL-compatible runtime.

No elevation or imagery data is redistributed in this repository.

## Architecture direction

Portable geospatial logic must not import `bpy`. Blender integration will be a
consumer of tested artifacts produced by the portable layers. Provider-specific
HTTP and parsing behavior will remain isolated from core geospatial models.

## Install in Blender

Build the extension ZIP with Blender 4.5:

```text
blender --command extension build --output-dir .artifacts/extension-build
```

Install the resulting ZIP from Blender through **Edit > Preferences > Get
Extensions > Install from Disk**. Enable Blender online access, select a cache
directory in the extension preferences, and open **3D View > Sidebar > Terrain**.
Run `Validate ROI`, `Discover Sources`, `Download Data`, and `Create Terrain` in
that order. `Balanced` is the recommended resource profile. `Large` permits a
larger processing budget but does not bypass the per-object mesh safeguards and
can exhaust RAM or GPU memory. The **Cache** panel reports disk use by category;
its cleanup actions require confirmation and are disabled while a job is active.
A useful manual check is to validate a small ROI, discover and download its
sources, create the terrain, adjust its displacement, save the `.blend`, and
open it again before testing a larger area.

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
through the first-party CNIG initialization and delivery endpoints.

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
