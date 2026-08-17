# Design — debug UI CSV formula neutralization

## Scope

One JS function gains a guard, one new mechanical lint pins it to its Python
twin, one test file gains behavioral coverage, and four documentation sites
stop asserting the hole is open (`SECURITY.md`, the `api-cli-server.md` spec
paragraph, the A-018 ledger follow-up line, and the CLAUDE.md lint inventory —
enumerated by `git grep`, not guessed; see implement.md step 6). No new
endpoint, no new payload, no server behavior change: `downloadCSV` is
client-side, so nothing on the wire moves.

## Current state (verified at HEAD 916422a)

- `src/anomaly_metric_creator/server_debug_ui.py:560` —

  ```js
  function csvCell(value) {
    const text = String(value ?? "");
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  }
  ```

- `downloadCSV` (`server_debug_ui.py:564`) is `csvCell`'s only consumer;
  `exportUnsupportedCsv` (`server_debug_ui.py:1171`) is `downloadCSV`'s only
  call site. `grep -n "csvCell\|downloadCSV"` returns exactly those four hits,
  so the guard has one seam.
- `src/anomaly_metric_creator/trace_bundle.py:28` —
  `_CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")`, applied by
  `_neutralize_csv_cell` (`trace_bundle.py:31`) at `trace_bundle.py:243`.
- `SECURITY.md:151` — "Note that the debug UI's own client-side CSV download
  does not yet carry this guard."
- `tests/test_debug_ui_javascript.py` already extracts `DEBUG_HTML`'s embedded
  scripts with an `HTMLParser` subclass and runs `node --check` over them,
  skipping when `shutil.which("node")` is `None`. This is the answer to the
  PRD's open testing question: the harness exists, it only needs a second test
  that *evaluates* rather than parses.
- `tools/check_module_size.py --list` reports `server_debug_ui.py 1189 / 1189`
  — enrolled at its exact current size, zero headroom.

## Decisions

### D1 — neutralize before quoting, inside `csvCell`

```js
function csvCell(value) {
  const text = String(value ?? "");
  // Lockstep with trace_bundle._CSV_FORMULA_TRIGGERS; see
  // tools/check_csv_formula_trigger_lockstep.py.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}
```

Neutralization runs first so the apostrophe lands *inside* the quotes when the
cell is also quoted — `'=a,b` becomes `"'=a,b"`, which is what a spreadsheet
must see. Reversing the order would emit `'"=a,b"`, where the quote, not the
apostrophe, is the first character of the field, and the guard is lost.

Idempotent by construction: after prefixing, the first character is `'`, which
is not in the trigger set — the same reason `_neutralize_csv_cell` is
idempotent, and `tests/test_trace_bundle.py:497` already pins that property on
the Python side.

Applied inside `csvCell` rather than at `downloadCSV`'s column list, so it
covers the header row and every future column with no allowlist — matching the
posture `_neutralize_csv_cell`'s docstring argues for. A future second
`downloadCSV` call site inherits the guard for free.

Trigger set is `=`, `+`, `-`, `@`, tab, CR. In the JS character class `-` is
escaped (`\-`) so it is a literal, not a range.

### D2 — a mechanical lint, not a comment, for lockstep

The PRD prefers a lint per the repo's greppable-pattern rule, and the pattern is
greppable: a Python tuple literal and a JS character class, both a fixed set of
single characters in one file each.

New `tools/check_csv_formula_trigger_lockstep.py`:

- Extracts `_CSV_FORMULA_TRIGGERS`'s literal from `trace_bundle.py` by parsing
  the module with `ast` and reading the assignment's `ast.literal_eval` value —
  not a regex over source text, so a reformat cannot spoof it.
- Extracts the JS character class from `server_debug_ui.py` by locating the
  marked `csvCell` guard line and unescaping the class body into a character
  set.
- Compares the two sets and reports drift.
- Exit codes follow the repo convention documented in every sibling guard:
  `0` in step, `1` drift (one diagnostic naming both sets and both files),
  `2` structural — either literal not found, or a file unreadable.
- Accepts optional path arguments defaulting to the repo-root files, so its
  test can point it at fixtures. This mirrors `check_ruff_lockstep.py`, the
  closest existing shape.

Both source files get a comment naming the other site regardless, so the lint
is discoverable from either end; the PRD's minimum and its preference are not
alternatives here.

`check_trace_payload_antipatterns.py` is the wiring template — the only other
guard whose `files:` regex selects a pair of `src/anomaly_metric_creator/*.py`
modules, and `check_guard_ci_coverage.py --list` reports it as
`needs=QUICK+FULL has=QUICK+FULL`. The new lint inherits the same lane
obligation.

### D3 — behavioral JS test through the existing node harness

`tests/test_debug_ui_javascript.py` gains two tests:

1. **Node-driven behavior** (skips without node, exactly like the existing
   syntax test): write the extracted script plus a small driver to `tmp_path`,
   run `node`, and assert `csvCell` output for each trigger character, for the
   quoting-plus-neutralization composition (`=a,b` → `"'=a,b"`), for
   idempotency, and for a benign value passing through untouched.

   The extracted debug-UI script cannot be imported as a module — it is an
   inline `<script>` body that immediately calls `$()` against a DOM. The
   driver therefore does not execute it: it slices the `csvCell` function
   source out of the extracted text and evaluates that one function. That keeps
   the test honest about *which* source it is asserting on (the served bytes)
   without needing a DOM shim.

2. **Node-independent assertion** over `DEBUG_HTML` that the trigger characters
   are present in the guard line. This is the PRD's sanctioned fallback and the
   only coverage on a runner with no node. It is weak on its own, which is why
   it is the second test and not the only one.

The lint (D2) is what actually prevents silent divergence; the tests prove the
guard behaves.

### D4 — bump the module-size ceiling in the same diff

`server_debug_ui.py` sits at its ratchet ceiling of 1189 with no headroom, and
D1 adds two lines inside a Python string literal holding the UI template. That
addition is **not separable** — it cannot be extracted to another module
without moving the entire embedded UI — so the sanctioned remedy is the
reviewed ceiling bump, per CLAUDE.md's "raise that module's ceiling in the same
diff when it is not". One line in `tools/check_module_size.py`'s `RATCHET`.

Do not decompose the debug UI to pay for this guard.

## Non-goals

- Decomposing `server_debug_ui.py` (tracked as its own ratchet debt entry).
- A JS test runner. The node harness already in the repo is sufficient for one
  pure function; introducing a package.json for this is disproportionate.
- Widening the guard to `downloadJSON` — JSON is not formula-interpreted by a
  spreadsheet, and the A-018 threat model is the CSV open path.

## Failure modes considered

- **Legitimate `-`-prefixed values pick up an apostrophe.** Accepted, and
  already accepted on the Python side for the same reason recorded in
  `_neutralize_csv_cell`'s docstring: harmless in a debug export. The exported
  columns are `fingerprint`, `count`, `first_seen`, `last_seen`,
  `guessed_intent`, and an example command — `count` is a positive integer,
  the timestamps are ISO strings, so no current column can be affected in
  practice.
- **The lint's JS extraction is brittle.** Mitigated by anchoring on a stable
  marker comment on the guard line rather than on the function body's shape,
  and by exit code `2` (structural) rather than `0` when the marker is missing
  — a refactor that loses the marker fails loudly instead of passing vacuously.
- **Node missing on a CI runner.** Test 1 skips, test 2 still runs, and the
  lint runs regardless. Coverage degrades but does not vanish.

## Rollback

Every change is additive and independent. Revert the commit; nothing persists,
no migration, no state. The guard has no runtime dependency on the lint.
