# ADR-0004: First-party CNIG direct delivery

- Status: Accepted
- Date: 2026-08-19
- Decision owners: Enrique Fuster and project maintainers
- Accepted: 2026-08-19
- Supersedes: ADR-0003

## Context

`descargaDirS3` generated structurally valid pre-signed URLs whose advertised
objects returned `NoSuchKey`. The product detail page still exposes an older
first-party workflow: initialize the download session with `initDescargaDir`,
then submit the authorized sequence to `descargaDir`.

Tests against MDT02 and MDS02 returned `image/tiff` directly from the official
CNIG host. Complete files were downloaded and independently opened by tifffile
and Rasterio.

## Decision

Use the first-party direct workflow:

1. establish a CNIG session and open the resource detail page;
2. POST the catalog sequence to `initDescargaDir`;
3. require `muestraLic=NO` and an identical authorized sequence;
4. POST that sequence to `descargaDir` using the same CNIG cookie jar;
5. refuse redirects and validate the response before atomic promotion.

If `muestraLic=SI`, stop with `DownloadAuthorizationRequired`. The client must
not imitate an unimplemented license-acceptance interaction.

## Consequences

- Runtime downloads remain on the first-party CNIG HTTPS host.
- The S3 hand-off parser and runtime policy are removed.
- A mismatch between requested and authorized sequence is a contract failure.
- Filename comparison tolerates only observed case and `_`/`-` differences.
- The endpoint currently ignores HTTP Range, so the client streams the complete
  file while keeping memory proportional to a 1 MiB block.
- Direct download remains an observed, non-public contract protected by fixtures
  and online checks.
