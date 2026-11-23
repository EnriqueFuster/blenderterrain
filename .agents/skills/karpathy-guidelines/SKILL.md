---
name: karpathy-guidelines
description: "Use when implementing, editing or refactoring code to avoid overengineering, preserve scope, surface material assumptions and define verifiable success criteria."
---

# Simple, focused implementation

## Understand before editing
- Read the relevant implementation, callers and current Git diff.
- Resolve facts from the repository and matching official documentation.
- State consequential assumptions. Ask only when a missing requirement blocks
  a correct choice; do not ask again for decisions already established.

## Choose the simplest complete solution
- Implement the requested behavior, including its acceptance criteria.
- Reuse the project's modules, provider interfaces, helpers and dependencies.
- Add a new abstraction only for a demonstrated current need. Preserve useful
  existing boundaries even when inlining them would shorten the diff.
- Prefer clear names and explicit control flow over dense one-liners.
- Avoid speculative options, future-proofing and unrequested features.
- Keep necessary input validation, error reporting and recovery behavior.
- Prefer standard or already installed tools when they meet the requirement.
  Do not replace established geospatial libraries with hand-written geometry
  or CRS code just to reduce dependencies.

## Make surgical changes
- Match the existing style and supported versions.
- Keep unrelated refactors, formatting and cleanup out of the change.
- Remove unused code introduced by your own changes. Report unrelated dead
  code separately instead of expanding the task.
- Preserve work already in progress; inspect pre-existing edits before touching them.

## Work toward evidence
- For a nontrivial task, state a short plan and observable success criteria.
- Use $tdd for meaningful new logic and regression coverage, following the
  boundaries in AGENTS.md. Scale the process to the change.
- Review the final diff for missing requirements and needless complexity.
- Use $verification-before-completion before reporting code work as complete.

<!-- Adapted for BlenderTerrain from https://github.com/multica-ai/andrej-karpathy-skills/blob/2c606141936f1eeef17fa3043a72095b4765b9c2/skills/karpathy-guidelines/SKILL.md. Upstream MIT terms are retained in LICENSE.txt. -->
