---
name: systematic-debugging
description: "Use when investigating a bug, failed test, unexpected Blender behavior, performance regression or build failure. Reproduce and investigate before changing the implementation."
---

# Systematic debugging

## 1. Investigate the cause
- Record the exact symptom, triggering input, command and relevant versions.
- Read the complete relevant error and traceback; retain the original evidence.
- Reproduce with the smallest representative case in an isolated environment.
- Inspect recent diffs, callers, configuration and dependency changes.
- Trace the invalid value or state back to its origin. See
  [root-cause-tracing.md](references/root-cause-tracing.md) when the failure
  appears far from its source.
- If reproduction is intermittent, measure frequency and check timing/state;
  see [intermittent-failures.md](references/intermittent-failures.md).
- Add focused diagnostics at relevant boundaries when necessary. Keep secrets
  and personal data out of logs.

## 2. Compare working and failing cases
- Find a working path or fixture and identify the relevant differences.
- For Blender, compare supported versions, execution context, modes and lifecycle.
- For geospatial behavior, check CRS, axis order, units, extent, origin, pixel
  alignment, NoData and source coverage before adding compensating offsets.

## 3. Test one hypothesis
- State a hypothesis and the observation that would confirm or reject it.
- Change one relevant variable at a time. Prefer a diagnostic probe over a
  speculative production edit.
- Rejected hypotheses are evidence to retain, not patches to stack.
- If the failure cannot be reproduced, investigate conditions and uncertainty;
  do not claim a proven cause from a single successful run.

## 4. Implement and verify the correction
- Use $tdd to add a failing regression test at an established project boundary
  when practical. A small reproducible Blender script or documented UI case is
  appropriate when an automated test cannot observe the behavior.
- Apply the smallest complete correction at the cause; preserve existing work.
- Run the reproduction again and the relevant regression checks.
- Use $verification-before-completion and distinguish observation from inference.

## Reassess repeated failures
After three failed correction attempts, stop stacking changes. Review the
evidence, discarded hypotheses and investigation strategy. Architecture may be
involved, but failed attempts alone do not prove an architectural defect.
Ask for a decision only if a material tradeoff or scope change needs it.

<!-- Adapted for BlenderTerrain from https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/skills/systematic-debugging/SKILL.md. Upstream MIT terms are retained in LICENSE.txt. -->
