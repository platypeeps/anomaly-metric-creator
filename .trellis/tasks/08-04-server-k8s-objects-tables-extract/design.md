# Design — Extract server_k8s_objects.py + server_k8s_tables.py (step 4)

## Closure audit (2026-08-04)

Symbol-precise audit of the k8s object-builder + Table-renderer cluster
against the epic's one-way rule (a moved leaf must never import
`server_ops`). Result: the **tables** leaf is clean; the **objects** leaf
is NOT clean as the epic's 2-module plan — the object builders call five
`server_ops`-resident helpers at runtime.

### Blocker helpers (moved builder → staying helper)

| helper | defn | own closure | staying callers | moving callers |
|---|---|---|---|---|
| `_string_dict` | 2882 | pure (`isinstance`/`str`/dict-comp) | 2316, 2840, 2846 | 6060, 6061 |
| `_snapshot_row_namespace` | 415 | `DEFAULT_NAMESPACE` (server_mutations, lower leaf) | 420, 1181 | 5464, 5586, 5838, 6068 |
| `_parse_user_timestamp` | 3611 | stdlib only | 106, 1587, 3626 | 6041 |
| `_k8s_list_resource_version` | 4769 | `state.mutations` attrs only (param) | 4751 | 4940 |
| `_snapshot_row_labels` | 1184 | **`DEFAULT_RELEASE` @41 (server_ops)** | 1173, 4294 | 5548, 5620, 5647, 5677 |

Four of the five have clean own-closures (stdlib / lower-leaf only). The
fifth, `_snapshot_row_labels`, additionally reads `DEFAULT_RELEASE`, a
helm-identity constant that lives in `server_ops` (peer of
`DEFAULT_NAMESPACE`, which already lives in `server_mutations`).

### Why a partial move / redrawn seam does not work

The object-builder cluster (`_k8s_metadata`, `_k8s_metadata_for_row`,
`_k8s_pod`, `_k8s_deployment`, `_k8s_daemonset`, `_k8s_statefulset`,
`_k8s_service`, `_k8s_replicaset`, `_k8s_endpointslice`, …) is pervasively
entangled with these accessors. There is no subset of builders whose
closure avoids all five, so "move only the clean builders" cannot carve a
one-way leaf. The seam genuinely requires the shared accessors to sit
**below** the objects leaf.

### Architectural note

Four of the five helpers are **not k8s-object-specific** — they serve the
staying snapshot/mutation/parse/helm layer too
(`_snapshot_row_namespace` → mutation keys + namespace filtering;
`_parse_user_timestamp` → general timestamp parsing;
`_string_dict` → generic label/annotation coercion;
`_k8s_list_resource_version` → also the step-5 REST facade). Pushing them
into a module named `server_k8s_objects.py` would invert the layering: the
core snapshot/helm code would reverse-import from a k8s-objects leaf. So
the honest home for the shared accessors is a **general support leaf**, not
the objects module.

## Options considered

- **Option A — shared `server_ops_support.py` leaf (recommended).**
  Move the 5 pure accessors (+ `DEFAULT_RELEASE`/`DEFAULT_CHART`, so
  `_snapshot_row_labels`'s closure is satisfied) into a new pure lower
  leaf. Both `server_ops` and the two new k8s leaves import downward from
  it. Clean one-way DAG; honest module names; also tidies helm's future
  step (helm's Secret path already needs `DEFAULT_RELEASE` +
  `_k8s_metadata`). Cost: adds a module the epic design.md did not list,
  and migrates a helm-identity constant, which ripples into the parked
  helm task's assumptions. ~3-module PR.

- **Option B — helpers inside `server_k8s_objects.py`.** Keeps the epic's
  2-module count; `server_ops` re-imports the 5 helpers from the objects
  leaf. Mechanically one-way, but architecturally inverted: server_ops's
  snapshot/mutation/helm/REST-facade code would depend on a k8s-objects
  leaf for a namespace reader and a timestamp parser. Drift against the
  "focused surface per leaf" convention.

- **Option C — tables-only now, defer objects+support.** Extract only the
  clean `server_k8s_tables.py` this PR; re-plan the objects leaf on top of
  a support leaf later. Lowest risk, but does NOT move
  `_k8s_metadata`/`_k8s_timestamp` down — so it fails the stated purpose of
  resequencing step 4 ahead of helm (unblocking helm's k8s deps).

## Recommendation

**Option A.** It is the only option that satisfies the epic's own one-way
constraint without layering inversion, matches the design.md Risks escape
hatch ("shared helpers move down into leaves"), and delivers the
resequencing goal (k8s primitives below helm). The costs — one extra
module + a constant migration — are the maintainer-owned epic-map change
that this task escalates before cutting.

## Deferred until decision

`implement.md`, the render-oracle build, and the parametrized AST
extraction wait on the seam-shape decision. All three options share the
same verification spine (render-oracle byte-diff + server-family tests +
splice-hazard grep); only the module set and move-order differ.
