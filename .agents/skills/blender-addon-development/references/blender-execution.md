# Blender execution and data rules

Adapted from EL4CTEO/blender-skills' blender-core. General rules apply only where
they match the actual supported Blender version and BlenderTerrain's conventions.

## Discover and report the runtime
Locate Blender using the project's existing setup or an actual executable path.
Check its version. The bundled read-only helper can report the runtime:

```text
blender --background --factory-startup --python-exit-code 1 --python .agents/skills/blender-addon-development/scripts/env_report.py
```

Run this command from the repository root. If Blender is not on PATH, invoke its
real absolute executable path. PowerShell needs the call operator before a quoted
executable path, for example: & "C:\actual\path\blender.exe" <arguments>.
The command above starts a separate disposable process; it is not an instruction
to reset an open user scene. The helper performs no scene, file or network mutation.

## Use the existing test workflow
For a project's real Blender test script, a typical invocation is:

```text
blender --background --factory-startup --python-exit-code 1 --python <actual-project-test-script>
```

Replace the placeholder with a script that exists. A headless result is not proof
of interactive panel behavior, modal interaction or a correct viewport appearance.
MCP is optional; do not install a connector just to run a background test.

## Data and context
- Prefer bpy.data/bmesh and bulk mesh operations over per-element bpy.ops loops.
- Respect object/edit mode boundaries when reading or updating geometry.
- If an operator is needed, establish the context required by the supported API.
  Do not guess that a background process has a 3D View area.
- Discover enum values and version support when uncertain instead of copying a
  rendering engine name or property from a different Blender version.
- Pair evaluated temporary mesh creation with the required cleanup.
- Avoid accessing IDs or modifiers after operations that free them.
- Preserve the project's object naming, scale and coordinate conventions.
- Resolve paths deliberately; never reset/delete unrelated data in live sessions.

## Additional upstream context
The add-on template was sourced from a revision whose documentation targets 5.x.
It is supplied only as a small extension-format example. Its manifest license,
minimum version, identifiers and UI choices do not define BlenderTerrain's policy.
