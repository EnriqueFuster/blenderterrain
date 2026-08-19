# Testing and fixture policy

## Test layers

- Unit tests are deterministic, offline, and use only repository fixtures.
- Portable integration tests use a local HTTP server and controlled responses.
- Oracle tests may use Rasterio or another development-only dependency.
- Online tests contact official services, are explicitly selected, and never run
  as part of the default test command.
- Blender tests begin in Phase 1 after the portable Phase 0 gates pass.

## Contract fixture rules

Fixtures preserve the minimum response fragment necessary to test behavior. They
must not contain:

- `Cookie` or `Set-Cookie` headers;
- authorization values, passwords, session identifiers, or CSRF secrets;
- local absolute paths or user names;
- diagnostic dumps containing complete unrelated HTML responses;
- manually supplied private coordinates.

Public test ROI coordinates are allowed only when intentionally documented as a
non-sensitive geographic test case. HTTP headers should be represented in a
small JSON metadata file only when the parser or validator needs them.

Every captured fixture must include a sibling provenance record with the source
URL, UTC capture date, sanitization notes, and a SHA-256 digest of the sanitized
content. Raw responses remain outside Git and are deleted after sanitization is
verified.

## Updating CNIG fixtures

1. Run the future capture command explicitly; normal tests never refresh data.
2. Store the raw response outside the repository.
3. Reduce and sanitize the response.
4. Run the fixture safety test.
5. Review the diff manually for identifiers and unrelated personal data.
6. Record contract changes in `docs/provider-cnig.md`.
7. Commit the fixture, provenance record, parser change, and tests together.

## Downloads

Elevation download experiments require an explicit opt-in flag and are limited
to one resource per invocation during Phase 0. Tests must not attempt to consume
the anonymous-download quota to discover its boundary.

