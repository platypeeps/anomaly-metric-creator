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
//
// Empty PATH entries are dropped rather than read as POSIX's implicit current
// directory: honouring them would let whatever directory the caller happens to
// be sitting in supply the helper this gate runs. The Python and shell
// forwarders narrow PATH the same way; see `_sd_pack_forward.py`.

import { spawnSync } from "node:child_process";
import { accessSync, constants, realpathSync, statSync } from "node:fs";
import { delimiter, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const TARGET = "sd-ai-command-pack-review-preflight.mjs";
const ACTIVE_ENV = "SD_PACK_FORWARD_ACTIVE";

// Stripping this directory closes the self-loop but not the mutual one: with two
// checkouts on PATH, each forwarder strips only its own directory and spawns the
// other's copy, which spawns back. This marker survives the spawn, so the second
// hop for the same target is refused. Keyed by target name, so a helper that
// legitimately invokes a different pack helper is unaffected.
if (process.env[ACTIVE_ENV] === TARGET) {
  process.stderr.write(
    `${TARGET} was already forwarded once; refusing to recurse. ` +
      "More than one checkout of these forwarders is on PATH.\n",
  );
  process.exit(2);
}

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

// Resolve explicitly rather than letting `spawnSync` search: the Python and
// shell forwarders both compare the resolved target against themselves before
// handing over, and doing the search here is what makes that comparison
// possible. Without it an alternate PATH route to this same file -- a symlinked
// directory that `realpathSync` above could not resolve, say -- would spawn a
// copy of this forwarder rather than the helper.
function resolveOnPath(name, search) {
  for (const entry of search.split(delimiter)) {
    if (!entry) continue;
    const candidate = join(entry, name);
    try {
      if (!statSync(candidate).isFile()) continue;
      accessSync(candidate, constants.X_OK);
      return realpathSync(candidate);
    } catch {
      // Not an executable file here; keep looking.
    }
  }
  return null;
}

const resolved = resolveOnPath(TARGET, searchPath);
if (resolved === null) {
  process.stderr.write(
    `${TARGET} is not resolvable on PATH.\n` +
      "Install or refresh sd-ai-command-pack, then rerun.\n",
  );
  process.exit(2);
}
if (resolved === selfPath) {
  process.stderr.write(`${TARGET} resolved to this forwarder; refusing to recurse.\n`);
  process.exit(2);
}

const result = spawnSync(resolved, process.argv.slice(2), {
  stdio: "inherit",
  env: { ...process.env, PATH: searchPath, [ACTIVE_ENV]: TARGET },
});

if (result.error) {
  process.stderr.write(
    `${TARGET} at ${resolved} could not be executed: ${result.error.message}\n`,
  );
  process.exit(2);
}
if (result.signal) {
  process.stderr.write(`${TARGET} terminated on signal ${result.signal}\n`);
  process.exit(2);
}
process.exit(result.status ?? 2);
