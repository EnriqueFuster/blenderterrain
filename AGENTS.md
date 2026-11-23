# BlenderTerrain — Codex instructions

<!-- blenderterrain-codex:begin -->
## Project

- This is an existing Blender add-on under active development.
- Inspect the relevant implementation, callers and current Git diff before editing.
- Preserve unrelated work and changes already in progress.
- Keep each change focused on the requested behavior and its acceptance criteria.
- Preserve supported Blender/Python versions, add-on identity and packaging unless
  changing them is explicitly part of the task.

## Simplicity and maintainability

- Implement the simplest complete solution to the current requirement.
- Prefer readable, explicit code over fewer lines or dense one-liners.
- Follow the existing architecture, naming conventions and dependency policy.
- Reuse existing provider interfaces, utilities and dependencies.
- Add abstractions, options or tools only for a demonstrated current need.
- Keep unrelated refactors, formatting and cleanup out of the change.
- Preserve necessary validation, error reporting and supported behavior.
- Use English for new file and directory names, code comments and docstrings.
- Explain reasons and constraints in comments rather than restating obvious code.

## Blender and geospatial behavior

- Keep numerical and geospatial processing independent from bpy where practical,
  without introducing an unrequested architectural rewrite.
- Preserve existing provider behavior when adding or extending data sources.
- Preserve CRS, axis order, units, NoData, raster alignment and scene origin.
- Respect existing tiling, cache, cancellation and memory constraints.
- Access bpy data only on Blender's main thread; follow the existing execution design.
- Keep registration and cleanup symmetric for the resources changed by the task.
- Treat bundled templates as references when extending the existing project.
- Check version-specific API guidance against documentation for the supported versions.
- Run Blender integration checks in an isolated process or temporary scene when practical.

## Skill routing

- Read the repository-local definitions at `.agents/skills/<name>/SKILL.md`.
  Use these adapted definitions when a global skill has the same name.
- Use `$karpathy-guidelines` for implementation and refactoring decisions.
- Use `$systematic-debugging` for bugs, failed checks and unexpected behavior.
- Use `$tdd` for meaningful new logic and regression fixes under the policy below.
- Use `$blender-addon-development` for Blender integration, lifecycle, UI and packaging.
- Use `$verification-before-completion` before claiming code changes are complete.
- Load only the skills and references relevant to the current task.

## Testing policy

- Existing public interfaces, provider contracts, data transformations and Blender
  integration points are authorized testing boundaries. Do not request approval
  again for testing within them; ask only about a material unresolved contract.
- Reuse existing tools and small deterministic fixtures.
- Derive expected results independently from the implementation: use a specification,
  worked example, known-good literal or independently validated reference.
- Test observable behavior rather than private helpers or tautological assertions.
- Verify bpy-dependent behavior inside a supported Blender runtime when available.
- Check cosmetic or documentation changes directly; do not add tests solely for them.
- Preserve existing code and work in progress even if it was not written test-first.

## Project context and validation commands

- Consult `.agents/skills/blender-addon-development/references/project-context.md`
  for actual project commands, supported versions and conventions.
- Discover missing facts from the manifest or bl_info, README, source, existing
  test/build configuration and CI. Keep that context file concise and current.
- Distinguish supported versions from the locally installed runtime.
- Record only commands and paths that actually exist, including their working directory.
- If a check is manual or unavailable, record that explicitly instead of inventing a command.
- Resolve routine choices using existing instructions and repository evidence.
  Ask only when missing information blocks a correct decision.

## Verification and handoff

- Run relevant existing checks. Broaden testing when the change or failures justify it.
- Reuse passing evidence for unchanged code; rerun checks when subsequent changes invalidate it.
- Review the final diff for bugs, missing requirements and unnecessary complexity.
- Distinguish Python tests, Blender integration, packaging and manual/UI evidence.
- Report what changed, which commands actually ran, their results and anything unverified.
<!-- blenderterrain-codex:end -->
