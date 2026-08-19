# Data source inventory

This inventory separates claims from the implementation specification from facts
verified by this repository. A source is not considered current merely because a
URL or observed value appears in the specification.

## Verification status

- Inventory created: 2026-08-19
- Specification research date: 2026-08-19
- Live contract verification by this repository: pending Phase 0B and 0E
- Local sample verification by this repository: pending Phase 0C

| Source | Intended version 1 use | Official reference | Current status |
|---|---|---|---|
| CNIG MDT02 second coverage | 2 m terrain elevation | <https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-terreno-mdt02-segunda-cobertura> | Pending live and sample verification |
| CNIG MDS02 second coverage | 2 m surface elevation | <https://centrodedescargas.cnig.es/CentroDescargas/modelo-digital-superficies-mds02-segunda-cobertura> | Pending live and sample verification |
| PNOA Maximum Actuality WMS | ROI imagery texture | <https://www.ign.es/wms-inspire/pnoa-ma?SERVICE=WMS&REQUEST=GetCapabilities> | Pending capabilities and control-image verification |
| CNIG data policy | Attribution and permitted use | <https://centrodedescargas.cnig.es/CentroDescargas/politica-datos> | Pending legal-text recheck before release |
| CNIG FAQ | Anonymous download behavior | <https://centrodedescargas.cnig.es/CentroDescargas/faqs> | Pending current-policy and response verification |
| IDEE coverage API | Possible future elevation provider | <https://api-coverages.idee.es/> | Out of version 1 unless it exposes the required product |

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

