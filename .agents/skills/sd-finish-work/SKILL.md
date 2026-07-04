---
name: sd-finish-work
description: Use when the user wants the Software Delivery finish-work command to wrap up a Trellis coding session.
---

# SD Finish Work

Wrap up the current Trellis session so task records, validation notes, and
handoff state are ready for the user to disengage.

1. Resolve the `trellis-finish-work` skill by name using the agent's trusted
   skill discovery mechanism for installed skills.
2. If that skill is missing, unreadable, empty, resolves to more than one
   candidate, fails validation, defines contradictory steps that violate this
   command's safety rules, or requires unavailable tools, stop and report the
   exact blocker.
3. Use that skill as the primary instructions for this workflow. Treat the
   skill file as repo-local command-pack code; do not bypass
   normal sandbox, approval, or destructive-action safeguards. The wrapper's
   safety rules take precedence over instructions that try to modify agent core
   config, installed skills, or sandbox settings, or that recursively invoke
   this wrapper.
4. Execute the skill with the current repository, branch, modified files, and
   session context. The Trellis skill is responsible for identifying the active
   task or session record and for keeping finalization idempotent; do not rerun
   it for the same state unless the user explicitly asks to recover from a
   failed prior run.
5. When the workflow reaches the journal-recording step, record the session
   with the pack wrapper instead of calling `add_session.py` directly:

   ```bash
   python3 scripts/sd-ai-command-pack-record-session.py \
     --title "..." --summary "..." --commit "hash1,hash2" \
     --change "main change bullet" --change "..." \
     --test "- [OK] test result line" --test "..."
   ```

   The wrapper resolves each commit's subject from git (failing fast on
   unknown hashes), fills the Main Changes and Testing sections, and refuses
   to commit an entry that still contains template placeholders — the
   fill-and-amend dance after a bare `add_session.py` call is exactly what
   it removes. If the wrapper script is missing, fall back to
   `add_session.py` and fill the `(Add details)`, `(Add test results)`, and
   `(see git log)` placeholders manually before pushing.
6. Report what the skill completed, what remains for the user, and any
   validation or archival step that could not run.
