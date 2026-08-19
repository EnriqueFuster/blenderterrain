# CNIG portal provider research

This document records observed behavior of the non-public HTML contract used by
the CNIG download portal. It is evidence for an isolated adapter, not a claim
that CNIG supports these endpoints as a stable API.

## Observation on 2026-08-19

Read-only requests were made with an identifiable BlenderTerrain user agent. No
raster resource or download endpoint was requested.

| Observation | MDT02 | MDS02 |
|---|---:|---:|
| Product page status | 200 | 200 |
| `codAgr` | `MOMDT` | `MOMDT` |
| `codSerie` | `MDT02` | `MDS02` |
| Advertised resources | 8308 | 8153 |
| Advertised format | COG | COG |
| Valencia bbox result count | 2 | 2 |

The public test bbox is `[-0.39, 39.46, -0.37, 39.48]` in EPSG:4326. Each
result contained one native ETRS89 UTM variant and one WGS84 variant.

Observed native resources:

- `MDT02-ETRS89-HU30-0722-1-COB2.TIF`, sequential `10324426`;
- `MDS02-ETRS89-H30-PM-2-0722-1.TIF`, sequential `11610978`.

These identifiers are test observations and must not be hard-coded as product
defaults or assumed to remain downloadable.

## Method discrepancy

The product page's local `$.ajax` call does not declare an HTTP method, which
normally implies GET. Direct GET requests to `archivosSerie` returned HTTP 403,
including with normal AJAX headers. An `application/x-www-form-urlencoded` POST
using the same session and fields returned HTTP 200 and the expected catalog
fragment. The Phase 0 client therefore uses POST and treats the method as part of
the volatile provider contract.

## Download-flow discrepancy

The current product-page JavaScript contains references to `descargaDirS3` for
direct-download actions. This differs from the `descargaDir` endpoint recorded in
the implementation specification. Phase 0B does not call either endpoint. The
download contract must be researched again, deliberately, in Phase 0C before any
binary transfer is implemented.

## Failure semantics

- A 403 or other transport failure is provider unavailability, not no coverage.
- A catalog response with `totalArchivos=0` is a valid no-coverage result.
- A positive total with no recognized desktop result rows is a contract change.
- Missing product codes, totals, attribution fields, or COG format are contract
  changes rather than empty discovery results.

## Fixture policy

Fixtures under `tests/fixtures/portal_html` are reduced reconstructions of the
observed structural fragments. They do not contain raw page dumps, cookies,
analytics, or response headers. Each HTML fixture has a provenance JSON file and
a verified SHA-256 digest.
