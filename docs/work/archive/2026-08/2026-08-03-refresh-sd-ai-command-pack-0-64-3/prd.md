---
title: Refresh sd-ai-command-pack to 0.64.3
status: done
created: 2026-08-03
branch: refresh-sd-ai-command-pack-0.64.3
---
# Refresh sd-ai-command-pack to 0.64.3

## Release identity (immutable)
- Target version: `0.64.3` (from vendored pack manifest.json).
- Nature: corrective release hardening the helper-loader TOCTOU vulnerability.
- Current installed version: `0.64.0`.

## Managed scope
Installer-managed refresh only. Touch only paths written by the sd-ai-command-pack
installer and its deterministic preparation output. No hand edits to product code.

## Acceptance criteria
- Installed pack version reads `0.64.3`; install audit passes.
- Only installer-managed paths changed; focused review-preflight check passes.
- PR opened on branch `refresh-sd-ai-command-pack-0.64.3` targeting `main`, green and comment-clean.

## Finish-work expectation
Archive this task and record the session journal (real commit title, no
placeholder left in the pushed journal row) bundled into the published head so
the completion finish-work receipt validates at the reviewed head.
