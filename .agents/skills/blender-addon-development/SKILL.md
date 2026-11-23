---
name: blender-addon-development
description: "Use when changing a Blender add-on's bpy integration, lifecycle, operators, panels, properties, dependency packaging or distribution. Inspect the existing project's supported Blender/Python versions before choosing APIs."
---

# Develop the existing BlenderTerrain add-on

## Establish the actual project context
Read [project-context.md](references/project-context.md), then inspect the
repository's manifest or bl_info, README, packaging scripts and CI when needed.
Distinguish the supported version range from the locally installed runtime.
Upstream examples mention Blender 5.x; they do not authorize a version upgrade
or migration. Check version-specific APIs in the matching official Blender docs.

Read [blender-execution.md](references/blender-execution.md) for execution and
data/context rules. These are the relevant adapted blender-core resources;
no separate blender-core, asset-QA, modeling or MCP skill is required.

## Extend the project in place
- Preserve its add-on ID, public properties, module layout, provider contracts
  and packaging unless changing them is explicitly part of the task.
- The bundled [template](assets/addon_template/__init__.py) is reference material
  for missing functionality, not a replacement for existing source files.
- Preserve CRS, axis order, units, raster/texture alignment, scene origin and
  georeferencing. Do not apply arbitrary object transforms as a generic cleanup.
- Follow the relevant existing naming conventions and supported APIs.

## Lifecycle, UI and execution
- Keep bpy access on Blender's main thread. Use the existing project's job,
  process, timer or modal design for long work; keep the interface responsive.
- Prefer the Data API for bulk data operations when practical. Use operators
  only with a valid execution context and check their result.
- Make register/unregister behavior symmetric for the resources touched by the
  change: classes, properties, handlers, timers, menus, keymaps and draw callbacks.
- Repeated activation and cleanup must not leave stale registrations.
- Avoid retaining Blender data references across undo/file-load boundaries when
  they may become invalid; resolve the intended data safely when needed.

## Dependencies and packaging
- Keep the current add-on/extension format. Do not remove bl_info or introduce a
  manifest solely because an upstream example uses the extension format.
- For an extension, respect its declared permissions and Blender's online-access
  setting when implementing network behavior. Avoid external work during import
  or registration.
- Reuse the existing dependency policy. For extension wheels, verify Python ABI,
  operating system and architecture compatibility. Do not introduce runtime pip
  installs or change the license/distribution policy as part of routine edits.
- Use the real packaging commands from project-context.md, not upstream sample paths.

## Verification
- Use $tdd for meaningful behavior/regression coverage and existing project fixtures.
- Exercise relevant lifecycle/operator/data behavior in supported Blender versions.
  Background mode does not establish that every UI interaction works.
- For geometry or textures, check the changed output's relevant numerical
  properties and inspect it visually when appearance/alignment is part of success.
  Whole-scene retopology, asset audits or renders are not default requirements.
- Use $verification-before-completion and clearly report runtime coverage limits.

<!-- Adapted for BlenderTerrain from https://github.com/EL4CTEO/blender-skills/blob/1a1cc6c49312f9f370aae86f52b7b907f29c94f6/skills/blender-addon-development/SKILL.md. Upstream MIT terms are retained in LICENSE.txt. -->
