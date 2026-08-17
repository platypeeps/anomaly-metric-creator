#!/usr/bin/env node
// Forward to the machine-installed sd-ai-command-pack helper of the same name.
//
// Why this file exists: the pack's own `sd-check` resolves its shipped helpers
// only at `<repo>/scripts/sd-ai-command-pack-<name>` and requires a regular
// file there (a symlink is rejected). Since the repo moved to a thin pack
// install, the installer no longer places those files, so every builtin row
// reported `unavailable` and the aggregate never reached `passed` -- which
// fails `sd-review scope=pr` closed for every pull request. See
// `docs/DEVELOPMENT_CYCLE.md` for why the two simpler fixes do not work.
//
// Resolution is by name on PATH rather than by absolute path so a pack version
// bump does not strand this file on a stale install directory. This script's
// own directory is removed from the search path first: it shares its basename
// with its target, so a checkout that puts `scripts/` on PATH would otherwise
// resolve this forwarder to itself and spawn-loop forever.

import { spawnSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { delimiter, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const TARGET = "sd-ai-command-pack-review-preflight.mjs";

const selfPath = realpathSync(fileURLToPath(import.meta.url));
const selfDir = dirname(selfPath);

const searchPath = (process.env.PATH ?? "")
  .split(delimiter)
  .filter((entry) => {
    if (!entry) return false;
    try {
      return realpathSync(entry) !== selfDir;
    } catch {
      return true;
    }
  })
  .join(delimiter);

const result = spawnSync(TARGET, process.argv.slice(2), {
  stdio: "inherit",
  env: { ...process.env, PATH: searchPath },
});

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
