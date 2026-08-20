# Data source inventory

This inventory separates claims from the implementation specification from facts
verified by this repository. A source is not considered current merely because a
URL or observed value appears in the specification.

## Verification status

- Inventory created: 2026-08-19
- Specification research date: 2026-08-19
- Live CNIG catalog verification by this repository: completed for the Phase 0B
  Valencia query on 2026-08-19; see `provider-cnig.md`
- Local sample verification by this repository: completed on 2026-08-19 for one
  MDT02 and one MDS02 delivered through the first-party CNIG endpoint
- PNOA catalog and full-file delivery verification: completed on 2026-08-20

| Source | Intended version 1 use | Official reference | Current status |
|---|---|---|---|
| CNIG MDT02 second coverage | 2 m terrain elevation | <https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-terreno-mdt02-segunda-cobertura> | Catalog, direct delivery, and sample verified |
| CNIG MDS02 second coverage | 2 m surface elevation | <https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-superficies-mds02-segunda-cobertura> | Catalog, direct delivery, and sample verified |
| CNIG PNOA Maximum Actuality | Full source orthophoto | <https://centrodedescargas.cnig.es/CentroDescargas/ortofoto-pnoa-maxima-actualidad> | Catalog, direct delivery, and sample verified |
| PNOA Maximum Actuality WMS | ROI imagery texture | <https://www.ign.es/wms-inspire/pnoa-ma?SERVICE=WMS&REQUEST=GetCapabilities> | Pending capabilities and control-image verification |
| CNIG data policy | Attribution and permitted use | <https://centrodedescargas.cnig.es/CentroDescargas/politica-datos> | Pending legal-text recheck before release |
| CNIG FAQ | Anonymous download behavior | <https://centrodedescargas.cnig.es/CentroDescargas/faqs> | Pending current-policy and response verification |
| IDEE coverage API | Possible future elevation provider | <https://api-coverages.idee.es/> | Out of version 1 unless it exposes the required product |

## Verified elevation samples

The binary files remain in the ignored local research directory and are not
redistributed by the repository.

| Product | Bytes | SHA-256 | Shape | Transform origin | CRS |
|---|---:|---|---:|---|---|
| MDT02 Valencia 0722-1 | 108243982 | `E6A47A867C6057807805E6D4F8039041322B22FA42B10A13BEAE363C676F618D` | 7355 × 4881 | 713115, 4375657 | EPSG:25830 |
| MDS02 Valencia 0722-1 | 114851269 | `3C176683099D6F81D8C6107A4192B96D1B86862CD73B7F926430C46CDD52B1DA` | 7163 × 4688 | 713115, 4376093 | EPSG:25830 |

Both are little-endian BigTIFF, single-band Float32, Deflate-compressed, tiled
512 × 512, with 2 m pixels, NoData `-32767`, and overview factors 2, 4, and 8.
Their different dimensions and transforms confirm that MDT/MDS alignment must be
geospatial rather than array-index based.

## Verified orthophoto sample

The 2024 Valencia resource `PNOA-MA-OF-ETRS89-HU30-H25-0722-1.TIF`
(sequential `12570809`) is 816752939 bytes with SHA-256
`B5F1FF991D6FB4A7A6D8F349E04780AF5F02AC5EA084423E3FA0EC30B6325C38`.
It is a 58880 by 39168, three-band RGB BigTIFF in EPSG:25830 with 0.25 m pixels,
256 by 256 JPEG-compressed tiles, and overview factors 2 through 256. CNIG
advertises it as COG; tiled window reads and overviews were verified, but no
independent COG conformance validator was available in the development environment.

## Values that must not become silent constants

The implementation specification records observed product codes, inventory
counts, TIFF encodings, NoData values, CRS variants, WMS image sizes, and portal
form fields. These are hypotheses for Phase 0 tests, not unconditional runtime
constants. Provider defaults must be verified against the live product page and
raster metadata must be inspected per file.

Every future verification entry must record:

- UTC timestamp;
- exact official URL or local sample identity;
- request purpose without cookies or credentials;
- response status and relevant sanitized metadata;
- fixture or test that preserves the observation;
- contract differences from the previous observation.
