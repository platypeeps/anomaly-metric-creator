#!/usr/bin/env node
// Forwarder to the machine-installed sd-ai-command-pack helper of the same name.
//
// Why this file exists: the pack's own `sd-check` resolves its shipped helpers
// only at `<repo>/scripts/sd-ai-command-pack-<name>` and requires a regular
// file there (a symlink is rejected). Since the repo moved to a thin pack
// install, the installer no longer places those files, so every builtin row
// reported `unavailable` and the aggregate never reached `passed` -- which
// fails `sd-review scope=pr` closed for every pull request. Registering an
// equivalent command in `.sd-ai-command-pack/check.json` does not help: the
// builtin rows are emitted regardless and `unavailable` outranks `passed` in
// the aggregate.
//
// Resolution is by name on PATH rather than by absolute path so a pack version
// bump does not strand this file on a stale install directory.

import { spawnSync } from "node:child_process";

const TARGET = "sd-ai-command-pack-review-preflight.mjs";

const result = spawnSync(TARGET, process.argv.slice(2), { stdio: "inherit" });

if (result.error) {
  process.stderr.write(
    `${TARGET} is not resolvable on PATH: ${result.error.message}\n` +
      "Install or refresh sd-ai-command-pack, then rerun.\n",
  );
  process.exit(2);
}
if (result.signal) {
  process.stderr.write(`${TARGET} terminated on signal ${result.signal}\n`);
  process.exit(2);
}
process.exit(result.status ?? 2);
