# Mock external boundaries deliberately

Adapted from Matt Pocock's boundary-mocking guidance.

Prefer real core logic with tiny local inputs. Stub network responses, clocks,
randomness or other external systems when necessary for deterministic tests.
Use temporary files for small file-based tests when the actual file behavior matters.

Preserve the provider's real response structure at the HTTP boundary. Do not
replace your parser, transformation or selection logic with a mock of itself.

Reuse existing injection points or fixtures. Avoid building a generic client,
factory or parallel hierarchy solely for testing.

Blender API availability, operator context, registration and UI behavior require
checks inside a supported Blender version. A mocked bpy object cannot establish
that those integrations work.
