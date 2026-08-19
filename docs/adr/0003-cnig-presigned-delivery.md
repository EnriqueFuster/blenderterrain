# ADR-0003: CNIG-issued pre-signed object delivery

- Status: Superseded by ADR-0004
- Date: 2026-08-19
- Decision owners: Enrique Fuster and project maintainers
- Accepted: 2026-08-19

## Context

The current CNIG portal does not return TIFF bytes from `descargaDirS3`. It
returns a small HTML hand-off page containing a short-lived, AWS-signed HTTPS URL
and JavaScript that navigates the browser to that URL. The observed object host is
outside the first-party IGN/IDEE/CNIG host allowlist.

Adding all of `amazonaws.com` to the normal network allowlist would grant much
more authority than one CNIG download requires. Rejecting object storage entirely
would make automatic downloads impossible while this is the portal's official
delivery mechanism.

## Decision

Treat the URL as a narrow, temporary capability issued by a verified CNIG
response, not as a generally trusted provider host.

The client will:

- parse exactly one expected hidden field from a size-limited CNIG HTML response;
- require HTTPS, the exact observed object host, the expected TIFF object name,
  AWS Signature Version 4 fields, and a lifetime of no more than two hours;
- keep the signed URL only in memory and never log, persist, or include it in an
  exception;
- download through a separate opener with no CNIG cookie jar or referrer;
- refuse redirects from the temporary URL;
- apply response-size, content-type, filename, TIFF-header, and atomic-promotion
  checks before accepting the resource;
- fail explicitly if the host or authorization shape changes.

Catalog and storage filenames may differ only in ASCII case and the observed
equivalence between `_` and `-`. All remaining filename components must match.

## Consequences

- CNIG cookies cannot leak to object storage through the implemented flow.
- A changed hand-off cannot redirect the client to an arbitrary external host.
- The exact storage host is volatile provider configuration and requires a
  contract update if CNIG migrates buckets or regions.
- Online checks on 2026-08-19 returned `NoSuchKey` for three advertised objects.
  This is treated as provider inconsistency; it does not relax validation and is
  not reported as no coverage.

## Superseding evidence

The first-party `initDescargaDir` and `descargaDir` flow was subsequently tested
and returned complete TIFF resources for both MDT02 and MDS02. ADR-0004 replaces
the S3 capability design as the runtime path. This record is retained to explain
why `descargaDirS3` is not followed.

## Rejected alternatives

- General allowlisting of `amazonaws.com`: excessive network authority.
- Parsing the JavaScript redirect: unnecessary and more fragile than parsing the
  dedicated hidden field.
- Sending the CNIG session opener to S3: risks disclosing first-party cookies.
- Silently falling back to arbitrary redirects: removes destination control.
