# Housekeeping

Run post-merge Trellis housekeeping for the current repository.

1. Read `.agents/skills/trellis-housekeeping/SKILL.md`.
2. Follow that skill exactly: run `bash scripts/trellis-housekeeping.sh`,
   confirm any current feature branch's PR is merged and matches the local
   branch before cleanup, switch to the default branch, fast-forward it, delete
   the merged development branch, prune refs, and verify the final repo state.
3. Report the condensed expected clean state plus any anomalies. Do not stage,
   commit, or push unrelated work.
