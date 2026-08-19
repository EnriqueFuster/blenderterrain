# ADR-0001: Independent implementation and licensing

- Status: Accepted
- Date: 2026-08-19
- Decision owners: Enrique Fuster and project maintainers
- Accepted: 2026-08-19

## Context

BlenderTerrain will initially import official terrain and imagery for Spain.
Thomas Kole's Blender Hoogtedata Addon demonstrates a comparable product flow,
but it targets different providers and spatial systems and is licensed under
AGPL-3.0.

Blender add-ons distributed through the Blender Extensions platform are expected
to use a GPL-compatible extension license. The project also needs an architecture
that can be maintained independently and can accommodate a future provider only
when a concrete data source justifies it.

## Decision

BlenderTerrain will be implemented independently and from scratch. No source
code, internal names, UI text, class structure, or control flow will be copied or
adapted from Thomas Kole's project.

The repository and public project identity are `BlenderTerrain`. Spain is the
only committed geographic scope for version 1. Provider boundaries will remain
explicit, but no generic multi-country framework will be built without a second
real provider use case.

Project code is licensed under `GPL-3.0-or-later`. Downloaded geographic data is
not redistributed and remains subject to provider terms and attribution.

## Consequences

- Prior art may be documented only at a behavioral and product-design level.
- All implementation and fixtures must have traceable, independent provenance.
- Portable geospatial modules must not import `bpy`.
- Spain-specific provider behavior must not leak into Blender UI or raster I/O.
- A later country provider may reuse proven interfaces, but does not justify
  speculative abstractions in version 1.

## Acceptance conditions

- Maintainer confirms the independent-implementation policy.
- The complete GPL-3.0-or-later license text is included before the first public
  release.
- Prior-art documentation records links and consultation dates without copying
  protected implementation material.
