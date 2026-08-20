# Raster reading boundary

## Current verified layout

The two manually downloaded CNIG samples have been inspected and decoded:

- MDT02: `MDT02-ETRS89-HU30-0722-1-COB2.TIF`
- MDS02: `MDS02-ETRS89-H30-PM-2-0722-1.TIF`

Both base images are little-endian BigTIFF files with one IEEE Float32 band,
Adobe Deflate compression, predictor `1`, and 512 by 512 pixel tiles. Their
NoData value is `-32767`. MDT02 has 150 tiles and MDS02 has 140 tiles.

`BigTiffFloatTileReader` intentionally supports only that observed layout. It
reads individual tiles and rectangular pixel windows, decompressing no pixels
outside the requested window's tiles. It also reads the projected EPSG code,
north-up affine transform, bounds, and PixelIsPoint half-pixel correction. It
does not interpret overviews or a different TIFF encoding.

## Verification performed

For both samples, the first tile and the partial tile at the lower-right image
edge matched Rasterio exactly, pixel for pixel. An 800 by 800 pixel window
crossing four tiles also matched Rasterio exactly. `tifffile` independently
decoded an isolated CNIG tile and reported the same base-image structure.
The EPSG code, affine transform, and bounds also match Rasterio exactly for both
samples.

Rasterio and tifffile remain development-only oracles. The package reader uses
only Python's standard library and NumPy.

## Consequence for future work

The next elevation-raster task is to convert projected bounds into pixel windows
and compose windows that cross source files. Any new source format must first be
inspected and either added as an explicit supported layout with tests or rejected
with `RasterFormatError`.
