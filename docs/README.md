# Data providers and products

This guide describes the external datasets known to BlenderTerrain. The bundled TOML catalog is
the executable source of truth used by the application; this page helps users judge suitability.
Resolution means nominal grid spacing, not guaranteed horizontal or vertical accuracy.

## Status terminology

- **Supported:** integrated into the normal workflow and covered by regression tests.
- **Experimental:** integrated end to end, but availability, coverage or performance still needs
  broader field validation.
- **Researched:** recorded for possible future integration and never offered as a candidate.

## Spain — IGN/CNIG and PNOA

Broad envelopes cover mainland Spain, Balearic Islands, Ceuta, Melilla and Canary Islands, but
exact sheet coverage is checked per ROI. Mainland products use ETRS89 UTM zones 29–31 and Canary
products use REGCAN95 UTM zone 28.

| Product | Kind | Grid spacing | Coverage/version | Status |
| --- | --- | ---: | --- | --- |
| MDT50CM | Derived DTM | 0.5 m | Third coverage; incomplete | Supported |
| MDT02 | Derived DTM | 2 m | Second coverage | Supported |
| MDT05 | Derived DTM | 5 m | Second coverage | Supported |
| MDT25 | Derived DTM | 25 m | Second coverage | Supported |
| MDT200 | Derived DTM | 200 m | Second coverage | Supported |
| MDS50CM | DSM | 0.5 m | Third coverage; incomplete | Supported |
| MDS02 | DSM | 2 m | Second coverage | Supported |
| MDS05 | DSM | 5 m | Second coverage | Supported |
| PNOA Máxima Actualidad | Orthophoto | Spatially variable; catalogued at 0.25 m | Latest published mosaic | Supported |

Sources: [CNIG Download Centre](https://centrodedescargas.cnig.es/CentroDescargas/) and
[PNOA WMS](https://www.ign.es/wms-inspire/pnoa-ma?SERVICE=WMS&REQUEST=GetCapabilities).
The catalog records CC BY 4.0 and attribution to the Instituto Geográfico Nacional de España.
Third-coverage products are not assumed to cover every ROI. PNOA capture date and native
resolution also vary spatially.

## France — IGN Géoplateforme

French products are requested from the IGN Géoplateforme raster WMS in Lambert-93 (EPSG:2154).
The current envelope covers metropolitan France and Corsica; valid pixels and exact local
availability are confirmed during acquisition.

| Product | Kind | Grid spacing | Main limitation | Status |
| --- | --- | ---: | --- | --- |
| RGE ALTI | Derived DTM | 1 m | Valid coverage is not guaranteed throughout the envelope | Experimental |
| MNS-Correl | DSM | 0.5 m | Coverage is heterogeneous | Experimental |
| BD ORTHO | Orthophoto | 0.2 m | Edition and coverage vary spatially | Experimental |

Sources: [RGE ALTI](https://geoservices.ign.fr/rgealti),
[BD ORTHO](https://geoservices.ign.fr/bdortho) and
[Géoplateforme WMS](https://data.geopf.fr/wms-r/wms). These products use the French Open Licence
2.0 with attribution to IGN France. Overseas territories currently use compatible global sources.

## Worldwide elevation

### GEDTM30 v1.1

- **Semantics:** modelled DTM, not a measured national bare-earth DTM.
- **Grid spacing:** 30 m.
- **Envelope:** 65°S–85°N, subject to missing or uncertain cells.
- **Auxiliary data:** uncertainty and a JRC Global Surface Water-derived mask are retained.
- **License:** CC BY 4.0; attribution to OpenGeoHub Foundation.
- **Status:** Experimental.

Source: [GEDTM30 v1.1](https://github.com/openlandmap/GEDTM30/releases/tag/v1.1).

### Copernicus DEM GLO-30

- **Semantics:** DSM containing surface features; never silently substituted for a DTM.
- **Grid spacing:** 30 m.
- **Coverage:** worldwide envelope, although the public AWS copy can omit individual tiles and
  marine cells need not contain useful terrain.
- **License:** Copernicus DEM terms; attribution “Contains modified Copernicus DEM data (2021)”.
- **Status:** Experimental.

Source: [Copernicus DEM on AWS](https://registry.opendata.aws/copernicus-dem/).

## Worldwide imagery

### ESA WorldCover Sentinel-2 composite 2021

- **Semantics:** static surface-reflectance composite, not live imagery or an orthophoto.
- **Grid spacing:** 10 m.
- **Envelope:** 60°S–84°N, with variable valid coverage near limits and water-only areas.
- **License:** CC BY 4.0; attribution to ESA WorldCover and modified Copernicus Sentinel data.
- **Status:** Experimental.

Source: [ESA WorldCover data access](https://esa-worldcover.org/en/data-access).

## Bathymetry — GEBCO 2026

GEBCO provides a global 15 arc-second seabed grid: approximately 463 m north–south, while
east–west spacing decreases with latitude. The compilation mixes measured and interpolated
sources, so grid spacing does not imply survey accuracy.

BlenderTerrain retains the Type Identifier quality grid and uses GEBCO only for marine cells.
Higher-resolution land remains untouched and seabed values are resampled onto the terrain grid
to produce continuous geometry. GEBCO is unsuitable for navigation, safety-critical work or
dependable inland-water depth.

Source: [GEBCO 2026](https://www.gebco.net/data-products/gridded-bathymetry-data/gebco2026-grid),
DOI `10.5285/4f68d5c7-45eb-f999-e063-7086abc036fa`. Attribution to the GEBCO Bathymetric
Compilation Group 2026 is required.

## Researched and potential future sources

These entries have no announced order or release target. They will remain unavailable until
their acquisition, coverage, licensing and regression gates are complete.

| Provider or product | Potential role | Current finding |
| --- | --- | --- |
| swisstopo swissALTI3D | Swiss derived DTM, nominally 0.5 m | Researched; STAC asset, edition and resolution selection remain unimplemented |
| swisstopo swissSURFACE3D Raster | Swiss DSM, nominally 0.5 m | Researched; progressive coverage needs item-level discovery |
| swisstopo SWISSIMAGE DOP10 | Swiss orthophoto, nominally 0.1 m | Researched; edition and exact coverage unresolved |
| Dynamic Sentinel-2 | Dated worldwide imagery | Deferred; needs cloud handling, mosaicking and temporal selection |
| OpenAerialMap | Opportunistic aerial imagery | Considered; heterogeneous licenses, quality and coverage need strict filtering |
| ArcticDEM and REMA | Polar elevation | Considered; polar CRS, tiling and semantics need a separate gate |
| EMODnet and regional bathymetry | Higher-detail regional seabed | Considered; datum and licensing vary by source |
| Additional national agencies | Authoritative local data | Evaluated independently; no country is promised or prioritised here |

Recorded Swiss sources: [swissALTI3D](https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissalti3d),
[swissSURFACE3D](https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swisssurface3d-raster),
[SWISSIMAGE](https://data.geo.admin.ch/api/stac/v1/collections/ch.swisstopo.swissimage-dop10) and
[swisstopo terms](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices).

## Selection and fallback rules

The ROI filters products by declared coverage but does not force a provider. Candidates remain
separate for DTM, DSM, imagery and bathymetry. Ranking may recommend an option using semantics,
effective resolution, authority, reliability, license and cost, but alternatives remain visible.

Selections require confirmation before an immutable plan is created. If a chosen provider fails,
BlenderTerrain reports the error and preserves reusable partial results; it never switches source
silently. Changing the ROI or product invalidates the previous plan.

## Verification and responsibility

Endpoints, catalog structures and coverage can change independently of BlenderTerrain. The TOML
catalog records last-verification dates, evidence URLs, acquisition contracts and limitations;
runtime discovery remains authoritative for the current ROI. Users must verify source suitability,
attribution and provider terms before publishing or distributing derived work.
