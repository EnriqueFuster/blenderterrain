# ADR-0002: Elevation and imagery data access

- Status: Accepted
- Date: 2026-08-19
- Decision owners: Enrique Fuster and project maintainers
- Accepted: 2026-08-19

## Context

Version 1 needs official 2 m MDT02/MDS02 elevation and an orthophoto covering the
same projected bounds. The observed CNIG portal exposes downloadable elevation
files but does not document its internal HTML endpoints as a public API. Complete
PNOA orthophoto files are unnecessarily large for interactive ROI texturing.

WMS returns rendered imagery rather than numerical elevation. Current coverage
services described in the implementation specification do not expose the exact
second-coverage 2 m products required by version 1.

## Decision

- Discover and download MDT02/MDS02 COG files through an isolated HTTP provider
  for the CNIG download portal.
- Parse HTML with `html.parser`; do not use Selenium or regular expressions as an
  HTML parser.
- Obtain the default PNOA texture through the official PNOA Maximum Actuality WMS
  using the same projected bounds as the terrain output.
- Keep local-file import as the recovery path when the portal changes or online
  access is unavailable.
- Use Rasterio only as a development oracle, not as a runtime dependency.
- Compare a constrained in-project TIFF reader with `tifffile` during Phase 0.
  Select the runtime backend only after numerical, memory, packaging, and license
  evidence is recorded.
- Require an explicit command-line flag for any elevation download experiment.

## Consequences

- The CNIG provider is a volatile adapter and requires sanitized contract
  fixtures and explicit `CatalogContractChanged` failures.
- WMS imagery is suitable for texture color but never for elevation values.
- Source rasters are cached as whole downloaded resources unless current tests
  demonstrate reliable server-side byte-range behavior.
- MDT02 and MDS02 are aligned through geotransforms and a target grid, never by
  filename, sheet identifier, dimensions, or array index.
- Online integration tests remain opt-in and avoid large routine downloads.

## Validation outcome

- The current portal discovery and anonymous first-party delivery contracts were
  exercised for MDT02, MDS02, and PNOA.
- Failed and size-limited downloads remove incomplete temporary files.
- Representative MDT02 and MDS02 BigTIFF windows and NoData matched Rasterio
  exactly.
- A projected PNOA WMS image matched the corresponding source pixels exactly.
- `tifffile` decoded the observed tiles and is license-compatible, but it does
  not replace the required spatial window and GeoTIFF logic. It remains a
  development oracle; the constrained reader is the runtime backend.

## Acceptance conditions

- Read-only discovery is reproducible without browser automation.
- Sanitized contract fixtures and parser tests exist.
- The raster backend decision is supported by measurements, not preference.
- Projected WMS control tests demonstrate dimensions and orientation.
