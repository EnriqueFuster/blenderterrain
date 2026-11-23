---
name: tdd
description: "Use for meaningful new logic or regression fixes using test-first development and behavior-focused tests. Follow the testing boundaries in AGENTS.md; do not impose tests on cosmetic or documentation-only changes."
---

# Behavior-focused test-first development

## Use the established project boundaries
Read the testing policy in AGENTS.md. Existing public interfaces, provider
contracts, data transformations and Blender integration points are authorized
testing boundaries by default. Record which boundary is being tested.
Ask only when a genuinely new or ambiguous public contract requires a decision.

## Test observable behavior
- Test through the relevant interface instead of private helper details.
- Derive expected values independently from the implementation: a specification,
  worked example, known-good literal or independently validated fixture.
- Use small deterministic inputs and the project's existing test tools.
- Do not add an abstraction, dependency or framework only to make a test possible.
- See [tests.md](tests.md) and [mocking.md](mocking.md) for project examples.

## Work in vertical slices
1. Choose one meaningful behavior and write its test or reproduction.
2. Run it and verify that it fails for the intended reason, not a setup error.
3. Implement the smallest complete change that satisfies that behavior.
4. Run it again, then the relevant existing regression checks.
5. Repeat for the next required behavior.

Do not bulk-write speculative tests for a design that has not been established.
Keep tests valid across internal refactors when the external behavior is unchanged.

## Preserve the existing project
- Existing code and work in progress are the starting point. Never delete or
  rewrite them merely because they were not written test-first.
- Characterize existing behavior before altering it when that reduces regression risk.
- Keep refactoring within the requested scope and protect behavior with tests.
- A text label, comment, documentation edit or equivalent low-impact cosmetic
  change normally needs an appropriate direct check, not a new test suite.
- Report unavailable Blender/UI checks explicitly; mocks do not prove them.

Review the diff, then use $verification-before-completion. An additional
code-review or codebase-design skill is not required by this workflow.

<!-- Adapted for BlenderTerrain from https://github.com/mattpocock/skills/blob/abeeaf95660ba9e8b7c753eb38f441ca707a8823/skills/engineering/tdd/SKILL.md. Upstream MIT terms are retained in LICENSE.txt. -->
