# SD planning adversarial review

When the current run creates or materially updates an active Trellis task's
`prd.md`, `design.md`, or `implement.md`, capture the pre-edit existence and
content hashes for those files. At the planning convergence boundary, before
requesting implementation approval or running `task.py start`, read and follow
[`../sd-ai-command-pack/planning-adversarial-review.md`](../sd-ai-command-pack/planning-adversarial-review.md).

Apply that contract once per coherent planning edit batch. Do not claim Codex
approval when its optional CLI lane is skipped or fails, and do not proceed
past an unresolved blocking concern.
