---
name: verification-before-completion
description: "Use before claiming code, tests, Blender integration or packaging work is complete, fixed or passing. Match each claim to verification evidence from the current code state."
---

# Verify before reporting completion

## Evidence gate
1. Identify the acceptance criteria and checks that actually establish them.
2. Run the relevant available commands against the current code state.
3. Read the output, exit status and any failures or warnings.
4. Compare the evidence to the claim you intend to make.
5. Report the verified result and any remaining gap.

| Claim | Relevant evidence |
| --- | --- |
| Tests pass | Actual result of the stated tests on the current code |
| Bug corrected | The original failing reproduction now passes and relevant regressions were checked |
| Blender integration works | The relevant behavior was exercised inside a supported Blender version |
| Package is valid | The applicable packaging/manifest validation and artifact inspection passed |
| Requirements met | Each relevant acceptance criterion is accounted for |

## Use proportionate checks
- Follow the concrete commands and conditions recorded in AGENTS.md.
- Run focused checks while iterating and broader relevant checks before handoff.
  Run the full suite when required by project policy or justified by the change.
- Retain evidence already gathered for the unchanged current code. Do not repeat
  successful checks unless later changes or unresolved concerns invalidate it.
- Distinguish lint, Python-only tests, Blender checks and visual/manual checks.
- For documentation/configuration-only edits, check the changed material instead
  of inventing an unrelated runtime validation stack.
- Report existing failures separately from failures introduced by the change.
- If a dependency, executable or external service is unavailable, state which
  claim remains unverified. Do not substitute confidence or mocks as proof.

## Handoff
State what changed, which checks actually ran and their result, and any concrete
remaining limitation. Do not claim that BlenderTerrain itself was tested when
only a template, standalone calculation or installation guide was checked.

<!-- Adapted for BlenderTerrain from https://github.com/obra/superpowers/blob/22f0b463e7a5e579904dd6211a520321035526f7/skills/verification-before-completion/SKILL.md. Upstream MIT terms are retained in LICENSE.txt. -->
