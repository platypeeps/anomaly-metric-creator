# Thinking Guides

Use these guides when a change risks duplication, drift, or a broken contract
between generator, server, tests, docs, and agent workflow files. Project
conventions live in `docs/spec/amc/backend/`; these guides are prompts for
checking the shape of a change before editing. Sources:
`docs/spec/amc/backend/index.md`; `.trellis/workflow.md`;
`.agents/skills/trellis-before-dev/SKILL.md`.

| Guide | Use When |
| --- | --- |
| [Code Reuse Thinking Guide](./code-reuse-thinking-guide.md) | You are adding a helper, constant, registry entry, parser branch, command renderer, test fixture, or platform adapter that might duplicate an existing source of truth. |
| [Cross-Layer Thinking Guide](./cross-layer-thinking-guide.md) | You are changing a flag, JSON/JSONL shape, schema field, command trace, server endpoint, output file, CI workflow, or platform adapter that crosses code/test/doc boundaries. |

Before committing a convention change, ensure the durable rule is captured in
the backend specs with source paths rather than only in chat, a task note, or a
platform-specific adapter. Sources: `docs/spec/amc/backend/documentation-review.md`;
`.trellis/tasks/archive/2026-06/06-25-consolidate-agent-docs-trellis/prd.md`;
`.agents/`; `.codex/`; `.claude/`; `.gemini/`; `.github/`; `.opencode/`.
