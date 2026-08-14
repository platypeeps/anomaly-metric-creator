#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { closeSync, constants as fsConstants, existsSync, fstatSync, lstatSync, openSync, readFileSync, readSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { TextDecoder } from 'node:util';

// Pack helpers are resolved next to this file so the preflight works from a
// vendored scripts/ directory, a plugin bin/, or a machine-wide install; only
// repository content is resolved against rootDir.
const scriptDir = dirname(fileURLToPath(import.meta.url));
const defaultRootDir = resolve(scriptDir, '..');
let rootDir = defaultRootDir;
let config = defaultConfig();
let failures = [];
let warnings = [];
let passes = [];
let installedTargetsCache;
let documentationGuardFilesCache;
const readTextCache = new Map();

const URI_SCHEME_PATTERN = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const MIN_NODE_VERSION = { major: 22, minor: 0, label: '22.0.0' };
// Git output ceiling for spawnSync calls that read diffs; Node's 1 MiB
// default truncates large diffs and surfaces as a spawn error.
const GIT_MAX_BUFFER_BYTES = 64 * 1024 * 1024;
const MAX_TRELLIS_TASK_LINKS = 100;
const MAX_TRELLIS_TASK_REFERENCE_LENGTH = 255;
const MAX_TRELLIS_PRIORITY_RATIONALE_LENGTH = 1000;
const MAX_BOOKKEEPING_FINDINGS = 100;
const MAX_BOOKKEEPING_ADVISORIES = 25;
const MAX_BOOKKEEPING_CHANGED_PATHS = 500;
const MAX_BOOKKEEPING_RECOVERY_COMMITS = 100;
// Stay well below Windows' roughly 32 KiB process command-line ceiling after
// accounting for executable, fixed arguments, quoting, and UTF-16 expansion.
const MAX_BOOKKEEPING_GIT_PATHSPEC_BYTES = 8 * 1024;
const MAX_BOOKKEEPING_SUCCESSOR_COMMITS = 50;
const MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS = 100;
const BOOKKEEPING_SCHEMA_VERSION = 1;
// Canonical lifecycle headings defined by the completion-versus-housekeeping
// contract (07-28-clarify-completion-housekeeping-obligations). Matched after
// case-folding and whitespace collapse; both are level-agnostic ATX headings.
const CANONICAL_ACCEPTANCE_HEADING = 'acceptance criteria';
const CANONICAL_POST_ARCHIVE_HEADING = 'post-archive handoff';
const MAX_BOOKKEEPING_ACCEPTANCE_ITEMS = 500;
const MAX_BOOKKEEPING_ACCEPTANCE_FINDINGS = 10;
const ATX_HEADING_RE = /^(#{1,6})[ \t]+(.*\S)[ \t]*$/;
const ACCEPTANCE_LIST_ITEM_RE = /^[ \t]*[-*][ \t]+(.*)$/;
const ACCEPTANCE_CHECKBOX_RE = /^\[([ xX])\](.*)$/;
const POST_ARCHIVE_CHECKBOX_RE = /^[ \t]*[-*][ \t]+\[[ xX]\]/;
const CODE_FENCE_RE = /^[ \t]{0,3}(`{3,}|~{3,})/;
// One repair string for both reporters. "Cite a spec instead" is not actionable
// on its own -- a consumer's specs are often all product-domain -- and dropping
// the `file` key is not an option either, because the ready gate still requires
// one real {file, reason} row, so a rationale-only manifest is unready rather
// than fixed.
const TRELLIS_TASK_CONTEXT_SELF_REFERENCE_REPAIR =
  'cites a path under its own task directory; task.py archive moves that directory, ' +
  'so the pointer dangles in the merged tree of the same bundle that publishes it. ' +
  'Repoint at a .trellis/spec/** path and move the substance into "reason", cite a ' +
  "sibling task's research/**, or move the facts into the pack's own task.";
const TRELLIS_TASK_STATUSES = new Set(['planning', 'in_progress', 'review', 'completed']);
const ACTIVE_TRELLIS_TASK_STATUSES = new Set(['planning', 'in_progress', 'review']);
const TRELLIS_TASK_PRIORITIES = new Set(['P0', 'P1', 'P2', 'P3']);
const ARCHIVE_TASK_JSON = /^\.trellis\/tasks\/archive\/\d{4}-\d{2}\/(\d{2}-\d{2}-[^/]+)\/task\.json$/;
const ACTIVE_TASK_JSON = /^\.trellis\/tasks\/(\d{2}-\d{2}-[^/]+)\/task\.json$/;
// Option sets for the shared `validateTaskLifecycleIdentity` helper (defined
// near its callers, close to `validateCompletionBundle`). Declared here, at
// module top level before the CLI dispatch below, because that dispatch runs
// synchronously as soon as this module is the Node entry point -- a `const`
// declared further down the file would still be in its temporal dead zone
// when that first synchronous call chain reaches it.
//
// Archive-move caller: `current` is the archived record. Both
// `current.status === 'completed'` and a non-empty `current.completedAt` are
// already independently enforced before this helper ever runs --
// `validateBookkeepingTaskDirectory(mapping.archiveDir, {archived: true})`
// raises `task_lifecycle_incomplete` for a non-completed archived status, and
// `validateTrellisBookkeepingMetadata` raises `task_metadata_invalid` for a
// missing `completedAt` on a completed record. Checking either again here
// would inject a reason code this call site never emitted before this
// extraction, so both stay off for this caller.
const ARCHIVE_MOVE_IDENTITY_OPTIONS = {
  sourceStatuses: ['in_progress', 'review'],
  checkCurrentStatus: false,
  checkSourceCompletedAtNull: false,
  checkCompletedAt: false,
  tolerateBranchNewlyRecorded: true,
  sourceCode: 'completion_source_lifecycle_invalid',
  identityCode: 'completion_archive_identity_changed',
};
// In-place caller: `current` is the same task directory's own live record
// (same path as `source`, different ref -- the file never moves). Decision 4:
// no status/branch transition tolerance for this shape.
const IN_PLACE_IDENTITY_OPTIONS = {
  sourceStatuses: ['in_progress', 'review'],
  checkCurrentStatus: true,
  currentStatuses: ['in_progress', 'review'],
  requireStatusEqual: true,
  checkSourceCompletedAtNull: true,
  checkCompletedAt: true,
  currentCompletedAtRule: 'null',
  tolerateBranchNewlyRecorded: false,
  sourceCode: 'completion_source_lifecycle_invalid',
  identityCode: 'completion_task_identity_changed',
};
const REVIEW_CODE_PATH_PATTERN = /\.(?:cjs|js|mjs|py|sh|ts|tsx)$/;
const REVIEW_WORKFLOW_PATH_PATTERN = /^\.github\/workflows\/[^/]+\.ya?ml$/;
const NON_PRODUCTION_CODE_DIRECTORY_SEGMENTS = new Set([
  'test',
  'tests',
  '__tests__',
  'fixture',
  'fixtures',
  'vendor',
  'vendored',
  'third_party',
  'node_modules',
  'generated',
]);
const MAX_REVIEW_RISK_PATHS = 5;
const MAX_CONFIGURED_REVIEW_RISK_SIGNALS = 20;
const MAX_CONFIGURED_REVIEW_RISK_SIGNAL_LENGTH = 120;
const REVIEW_LEARNINGS_PATH_PROVENANCE_FILE = 'docs/review-learnings.md';
const REVIEW_LEARNINGS_MANAGED_BLOCK_PATTERN =
  /<!-- sd-review-learnings:start -->[\s\S]*?<!-- sd-review-learnings:end -->/g;
const GENERATED_REVIEW_PATHS = new Set([
  'docs/fleet/candidate-validation.json',
  'docs/repomix-map.md',
  'templates/.agents/skills/sd-help/references/command-catalog.md',
]);
const STRUCTURED_INPUT_PATTERN =
  /(?:JSON\.parse|json\.(?:load|loads)|yaml\.(?:load|safe_load)|argparse|parse_[A-Za-z0-9_]+)/;
const DIRECT_BOUNDARY_SPLIT_PATTERNS = [
  /\b(?:process|sys)\.argv(?:\[[^\]\r\n]+\])?\s*\.\s*split\s*\(/,
  /\bprocess\.env(?:\.[A-Za-z_][A-Za-z0-9_]*|\[[^\]\r\n]+\])\s*(?:\?\.|\.)\s*split\s*\(/,
  /\b(?:os\.environ(?:\.get\s*\([^\r\n]*\)|\[[^\]\r\n]+\])|(?:os\.)?getenv\s*\([^\r\n]*\))\s*\.\s*split\s*\(/,
  /\b(?:[A-Za-z_$][A-Za-z0-9_$]*\.)?(?:readFileSync|readFile)\s*\([^\r\n]*\)\s*(?:\?\.|\.)\s*split\s*\(/,
  /\.\s*read_text\s*\([^\r\n]*\)\s*\.\s*split\s*\(/,
];
const REVIEW_RISK_CATEGORY_DEFINITIONS = [
  {
    id: 'structured-input-types',
    label: 'structured input and strict types',
    patterns: [STRUCTURED_INPUT_PATTERN, ...DIRECT_BOUNDARY_SPLIT_PATTERNS],
    variants: {
      good: 'valid structured input with exact scalar and container types',
      base: 'documented empty or default input',
      failure: 'malformed input and wrong types, including bool where an integer is required',
    },
  },
  {
    id: 'subprocess-command',
    label: 'subprocess and command execution',
    patterns: [
      /(?:\bspawn(?:Sync)?\s*\(|\bexec(?:File|Sync)?\s*\(|subprocess\.(?:run|Popen|check_call|check_output)|os\.system\s*\()/,
      /^\s*(?:-\s*)?run:\s*(?:[>|][-+]?\s*$|\S)/m,
    ],
    variants: {
      good: 'available command exits successfully',
      base: 'documented optional command is unavailable',
      failure: 'missing command, nonzero exit, and timeout',
    },
  },
  {
    id: 'environment-global-state',
    label: 'environment and process-global state',
    patterns: [
      /(?:process\.env|os\.environ|\bgetenv\s*\(|\bsetenv\s*\(|\bglobal\s+[A-Za-z_]|\bglobals\s*\()/,
      /^\s*env:\s*$/m,
    ],
    variants: {
      good: 'explicit environment value with state restored',
      base: 'unset or empty environment value and PATH',
      failure: 'success and exception paths restore process-global state',
    },
  },
  {
    id: 'path-filesystem',
    label: 'path and filesystem boundaries',
    patterns: [
      /(?:\bPath\s*\(|\bresolve\s*\(|\brealpath|\blstat|\bsymlink|readFileSync|writeFileSync|os\.path|pathlib)/,
    ],
    variants: {
      good: 'regular in-root path within size limits',
      base: 'missing, option-like, or traversal path',
      failure: 'symlink, oversized file, and TOCTOU replacement',
    },
  },
  {
    id: 'normalization-evidence',
    label: 'normalization and canonical evidence',
    patterns: [
      /(?:\bnormaliz(?:e|ed|es|ing|ation)\b|\bcanonical(?:ize|ized|ization)?\b|\bcasefold\s*\(|\bcompact_text\s*\(|\brev-parse\b|\bresolve(?:Commit|Ref)\b|hashlib|createHash\s*\(|\bsha(?:1|224|256|384|512)\b|\bdigest\s*\(|\bhexdigest\s*\(|\bchecksum\b|\bintegrity\b)/i,
    ],
    variants: {
      good: 'compare and persist one canonical value',
      base: 'equivalent raw and normalized values',
      failure: 'symbolic, missing, or noncanonical evidence',
    },
  },
  {
    id: 'diagnostic-redaction',
    label: 'diagnostic fidelity and redaction',
    patterns: [
      /(?:\b(?:redact(?:ed|ion)?|sanitiz(?:e|ed|ation)|mask(?:ed|ing)?)(?:_[A-Za-z0-9_]+)?\b|str\s*\(\s*(?:exc|error)\s*\)|\b(?:error|diagnostic)_message\b|\b(?:stderr|stdout)_detail\b)/i,
    ],
    variants: {
      good: 'actionable bounded diagnostic without secrets or host paths',
      base: 'expected empty detail uses a stable fallback',
      failure: 'raw exception, secret, or absolute-path material is redacted',
    },
  },
];
const REVIEW_RISK_CATEGORY_IDS = new Set(REVIEW_RISK_CATEGORY_DEFINITIONS.map((category) => category.id));
const JOURNAL_VALIDATION_FALLBACK_PATTERN =
  /^[ \t]*(?:[-*]\s*)?(?:\[OK\]\s*)?(Validation (?:was )?not recorded for this session\.)[ \t]*$/gim;
const JOURNAL_VALIDATION_SURFACE_PATTERN =
  /\b(?:quality gate|full[- ]check|tests?|test suite|lint|type[- ]?check|build|ci|codeql|playwright|validation|verification)\b/i;
const JOURNAL_VALIDATION_SUCCESS_PATTERN =
  /\b(?:pass(?:ed|es|ing)?|verified|validated|green|successful(?:ly)?|succeeded|completed)\b/i;
const JOURNAL_VALIDATION_NEGATION_PATTERN =
  /\b(?:fail(?:ed|ing|ure|ures)?|skip(?:ped|ping)?|pending|not|never|without|no)\b|\b(?:wasn't|weren't|didn't|isn't)\b/i;

// Declared before the module-level main run below: unlike function
// declarations, class bindings are not hoisted out of the temporal dead
// zone, and runCheck consults this class while checks execute.
class GitCommandError extends Error {}

export function runReviewPreflight(options = {}) {
  rootDir = resolve(options.rootDir || defaultRootDir);
  failures = [];
  warnings = [];
  passes = [];
  installedTargetsCache = undefined;
  documentationGuardFilesCache = undefined;
  readTextCache.clear();
  // Load config only after the result buffers are reset so a malformed config
  // file's fail() entry is reported instead of being wiped by the reset.
  config = loadConfig(rootDir, options.configPath);

  runCheck('package override sources of truth', checkPackageOverrides);
  runCheck('copied template diff disclosure', checkCopiedTemplateDiffDisclosure);
  runCheck('documentation path hygiene', checkDocumentationPathHygiene);
  runCheck('documentation path references', checkDocumentationPathReferences);
  runCheck('changed Trellis task metadata integrity', checkChangedTrellisTaskMetadata);
  runCheck('changed Trellis task topology semantics', checkChangedTrellisTaskTopologySemantics);
  runCheck('completed Trellis task location', checkCompletedTrellisTaskLocation);
  runCheck('Trellis task context manifests', checkTrellisTaskContextManifests);
  runCheck('Trellis planning placeholders', checkTrellisPlanningPlaceholders);
  runCheck('Trellis journal records', checkTrellisJournalRecords);
  runCheck('first-review risk sweep', checkReviewRiskSweep);
  runCheck('diff size warning', checkDiffSize);
  runCheck('tooling/generated scope advisory', checkScopeAdvisory);

  return {
    failures: [...failures],
    warnings: [...warnings],
    passes: [...passes],
  };
}

function defaultConfig() {
  return {
    documentationRoots: [
      'AGENTS.md',
      'README.md',
      'CLAUDE.md',
      'docs',
      '.github/instructions',
      '.github/prompts',
      '.trellis/spec',
      '.trellis/tasks',
    ],
    documentationExtensions: ['.md', '.mdx', '.prompt.md', '.toml', '.jsonl'],
    integrationPaths: [
      'AGENTS.md',
      'README.md',
      'docs/**',
      '.github/instructions/**',
      '.trellis/spec/**',
    ],
    referencePrefixes: [
      '.agent/',
      '.agents/',
      '.claude/',
      '.codebuddy/',
      '.codex/',
      '.cursor/',
      '.devin/',
      '.factory/',
      '.gemini/',
      '.gito/',
      '.github/',
      '.kiro/',
      '.kilocode/',
      '.opencode/',
      '.pi/',
      '.prism/',
      '.qoder/',
      '.reasonix/',
      '.sd-ai-command-pack/',
      '.trellis/',
      '.trae/',
      '.zcode/',
      'apps/',
      'docs/',
      'scripts/',
      'tests/',
    ],
    topLevelReferenceFiles: [
      '.dockerignore',
      '.gitignore',
      'AGENTS.md',
      'CLAUDE.md',
      'Dockerfile',
      'README.md',
      'package-lock.json',
      'package.json',
    ],
    ignoredReferencePrefixes: [
      '.build/',
      '.claude/',
      '.local/',
      'node_modules/',
    ],
    optionalReferencePaths: [
      '.sd-ai-command-pack/installed-targets.txt',
      '.sd-ai-command-pack/local-only.txt',
      '.sd-ai-command-pack/manifest.json',
      '.sd-ai-command-pack/pr-body-scope.json',
      '.sd-ai-command-pack/provenance.json',
      '.sd-ai-command-pack/review-preflight.json',
      '.trellis/.developer',
      '.trellis/.template-hashes.json',
      '.trellis/audit/ledger.md',
      'ARCHITECTURE.md',
      'ARCHITECTURE_OVERVIEW.md',
      'docs/ARCHITECTURE.md',
      'docs/ARCHITECTURE_OVERVIEW.md',
      'docs/TRELLIS_REVIEW_PR_PACK.md',
      'docs/repomix-map.md',
      'docs/review-learnings.md',
      'package.json',
      'scripts/check-review-preflight.mjs',
      'scripts/classify-ci-changes.sh',
      'scripts/classify_ci_changes.sh',
    ],
    copiedTemplateExtraPaths: [],
    allowedLinuxHomeUsers: [],
    reviewRiskCategorySignals: {},
    copilotReviewFileLimit: 300,
    diffSizeWarningLines: 20000,
    largeFileWarningLines: 5000,
    sourceReviewWarningLines: 1000,
    untrackedFileReadLimitBytes: 1048576,
  };
}

function loadConfig(root, explicitPath) {
  const merged = defaultConfig();
  const configPath = explicitPath || '.sd-ai-command-pack/review-preflight.json';
  const absoluteConfigPath = resolve(root, configPath);

  if (!existsSync(absoluteConfigPath)) {
    return merged;
  }

  let raw;
  try {
    raw = JSON.parse(readFileSync(absoluteConfigPath, 'utf8'));
  } catch (error) {
    fail(`${configPath} could not be parsed as JSON: ${error.message}`);
    return merged;
  }

  for (const key of [
    'documentationRoots',
    'documentationExtensions',
    'integrationPaths',
    'referencePrefixes',
    'topLevelReferenceFiles',
    'ignoredReferencePrefixes',
    'optionalReferencePaths',
    'copiedTemplateExtraPaths',
    'allowedLinuxHomeUsers',
  ]) {
    if (Array.isArray(raw[key])) {
      merged[key] = [...new Set([...merged[key], ...raw[key].filter((value) => typeof value === 'string')])];
    }
  }

  for (const key of ['diffSizeWarningLines', 'largeFileWarningLines', 'sourceReviewWarningLines', 'untrackedFileReadLimitBytes']) {
    if (Number.isFinite(raw[key])) {
      merged[key] = raw[key];
    }
  }

  if (raw.copilotReviewFileLimit !== undefined) {
    if (Number.isInteger(raw.copilotReviewFileLimit) && raw.copilotReviewFileLimit > 0) {
      merged.copilotReviewFileLimit = raw.copilotReviewFileLimit;
    } else {
      fail(`${configPath} copilotReviewFileLimit must be a positive integer.`);
    }
  }

  merged.reviewRiskCategorySignals = parseReviewRiskCategorySignals(raw.reviewRiskCategorySignals, configPath);

  return merged;
}

function parseReviewRiskCategorySignals(value, configPath) {
  if (value === undefined) {
    return {};
  }
  if (!isPlainObject(value)) {
    fail(`${configPath} reviewRiskCategorySignals must be an object keyed by a known boundary-risk category.`);
    return {};
  }

  const parsed = {};
  for (const [categoryId, signals] of Object.entries(value)) {
    if (!REVIEW_RISK_CATEGORY_IDS.has(categoryId)) {
      fail(`${configPath} reviewRiskCategorySignals contains unknown category ${categoryId}.`);
      continue;
    }
    if (!Array.isArray(signals) || signals.length > MAX_CONFIGURED_REVIEW_RISK_SIGNALS) {
      fail(
        `${configPath} reviewRiskCategorySignals.${categoryId} must be an array of at most ` +
          `${MAX_CONFIGURED_REVIEW_RISK_SIGNALS} literal strings.`,
      );
      continue;
    }

    const validSignals = signals
      .filter(
        (signal) =>
          typeof signal === 'string' &&
          signal.trim().length > 0 &&
          signal.length <= MAX_CONFIGURED_REVIEW_RISK_SIGNAL_LENGTH,
      )
      .map((signal) => signal.trim());
    if (validSignals.length !== signals.length) {
      fail(
        `${configPath} reviewRiskCategorySignals.${categoryId} entries must be nonblank strings no longer than ` +
          `${MAX_CONFIGURED_REVIEW_RISK_SIGNAL_LENGTH} characters.`,
      );
      continue;
    }
    parsed[categoryId] = [...new Set(validSignals)];
  }
  return parsed;
}

function printReviewPreflightResult(result) {
  for (const message of result.passes) {
    console.log(`PASS ${message}`);
  }

  for (const message of result.warnings) {
    console.log(`WARN ${message}`);
  }

  for (const message of result.failures) {
    console.log(`FAIL ${message}`);
  }

  console.log(`\nReview preflight: ${result.failures.length} failure(s), ${result.warnings.length} warning(s).`);
}

// Most recent git failure observed by bookkeepingChangedEntries, so the
// *_unavailable finding composers -- including the silent-probe callers
// that pass a discarding add callback -- can name the actual git error
// instead of a bare "could not inspect". Cleared at every
// bookkeepingChangedEntries entry: a status-0 malformed-output null must
// not inherit an older invocation's failure. Also reset per validator run
// in runBookkeepingValidator, alongside the other module state. Declared
// before the CLI dispatch below: module evaluation reaches that dispatch
// (and therefore runBookkeepingValidator's module-state reset) before any
// later top-level statement runs.
let lastBookkeepingGitFailure = null;

const GIT_FAILURE_STDERR_LIMIT = 200;

function boundedGitFailureStderr(stderr) {
  const line = String(stderr || '').trim().split('\n', 1)[0].trim();
  if (!line) return 'no stderr output';
  return line.length > GIT_FAILURE_STDERR_LIMIT
    ? `${line.slice(0, GIT_FAILURE_STDERR_LIMIT)}...`
    : line;
}

function gitFailureSuffix(commandArgs, status, stderr) {
  return ` (git ${commandArgs.join(' ')} exited ${status}: ${boundedGitFailureStderr(stderr)})`;
}

function describeGitFailure(prefix) {
  if (!lastBookkeepingGitFailure) return prefix;
  const failure = lastBookkeepingGitFailure;
  return `${prefix}${gitFailureSuffix(failure.commandArgs, failure.status, failure.stderr)}`;
}

if (isMainModule()) {
  const unsupportedNode = unsupportedNodeVersionMessage(process.version);
  if (unsupportedNode) {
    console.error(`error: ${unsupportedNode}`);
    process.exit(2);
  }

  if (['pre-archive', 'final-bundle', 'seeded-task'].includes(process.argv[2])) {
    const cli = parseBookkeepingCli(process.argv.slice(2));
    if (cli.error) {
      console.error(`error: ${cli.error}`);
      console.error(bookkeepingUsage());
      process.exit(2);
    }
    const result = runBookkeepingValidator(cli.options);
    if (cli.options.json) {
      console.log(JSON.stringify(result, null, 2));
    } else {
      printBookkeepingResult(result);
    }
    process.exit(result.status === 'valid' ? 0 : 1);
  } else if (process.argv.length > 2) {
    console.error(`error: unknown review-preflight command ${JSON.stringify(process.argv[2])}`);
    console.error(bookkeepingUsage());
    process.exit(2);
  } else {
    const result = runReviewPreflight();
    printReviewPreflightResult(result);

    if (result.failures.length > 0) {
      process.exit(1);
    }
  }
}

function isMainModule() {
  const argvPath = process.argv[1];
  if (!argvPath) {
    return false;
  }

  try {
    return realpathSync(fileURLToPath(import.meta.url)) === realpathSync(argvPath);
  } catch {
    return import.meta.url === pathToFileURL(argvPath).href;
  }
}

function bookkeepingUsage() {
  return [
    'usage:',
    '  sd-ai-command-pack-review-preflight.mjs',
    '  sd-ai-command-pack-review-preflight.mjs pre-archive --task-dir <active-task-dir> [--task-dir ...] [--repo <repo-root>] [--json]',
    '  sd-ai-command-pack-review-preflight.mjs final-bundle --mode <completion|planning> --base <commit> --head <commit> [--repo <repo-root>] [--json]',
    '  sd-ai-command-pack-review-preflight.mjs seeded-task --task-dir <active-task-dir> [--repo <repo-root>] [--json]',
  ].join('\n');
}

function parseBookkeepingCli(args) {
  const command = args[0];
  const options = {
    command,
    rootDir: defaultRootDir,
    taskDirs: [],
    json: false,
  };
  const single = new Set();

  for (let index = 1; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === '--json') {
      options.json = true;
      continue;
    }
    if (!['--task-dir', '--mode', '--base', '--head', '--repo'].includes(arg)) {
      return { error: `unknown bookkeeping validator option ${JSON.stringify(arg)}` };
    }
    const value = args[index + 1];
    if (!value || value.startsWith('-')) {
      return { error: `${arg} requires a non-option value` };
    }
    index += 1;
    if (arg === '--task-dir') {
      const normalized = normalizePathSeparators(value).replace(/^\.\//, '');
      if (!/^\.trellis\/tasks\/\d{2}-\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*$/.test(normalized)) {
        return { error: `--task-dir must name an exact active .trellis/tasks/MM-DD-name directory` };
      }
      if (!options.taskDirs.includes(normalized)) {
        options.taskDirs.push(normalized);
      }
      continue;
    }
    if (single.has(arg)) {
      return { error: `${arg} may be provided only once` };
    }
    single.add(arg);
    if (arg === '--mode') options.mode = value;
    if (arg === '--base') options.base = value;
    if (arg === '--head') options.head = value;
    if (arg === '--repo') options.rootDir = value;
  }

  if (command === 'pre-archive') {
    if (options.taskDirs.length === 0) {
      return { error: 'pre-archive requires at least one --task-dir' };
    }
    if (options.mode || options.base || options.head) {
      return { error: 'pre-archive does not accept --mode, --base, or --head' };
    }
  } else if (command === 'seeded-task') {
    // Exactly one: the stage validates the one task it just created, and a
    // multi-task invocation would report a pass/fail an operator cannot map back
    // to a single consumer lane.
    if (options.taskDirs.length !== 1) {
      return { error: 'seeded-task requires exactly one --task-dir' };
    }
    if (options.mode || options.base || options.head) {
      return { error: 'seeded-task does not accept --mode, --base, or --head' };
    }
  } else if (command === 'final-bundle') {
    if (!['completion', 'planning'].includes(options.mode)) {
      return { error: 'final-bundle requires --mode completion or --mode planning' };
    }
    if (!options.base || !options.head) {
      return { error: 'final-bundle requires both --base and --head' };
    }
    if (options.taskDirs.length > 0) {
      return { error: 'final-bundle derives task directories from the committed delta' };
    }
  }
  return { error: '', options };
}

export function runBookkeepingValidator(options = {}) {
  rootDir = resolve(options.rootDir || defaultRootDir);
  config = defaultConfig();
  readTextCache.clear();
  lastBookkeepingGitFailure = null;
  const findings = [];
  const advisories = [];
  let advisoriesDropped = 0;
  const evidence = {
    baseOid: null,
    headOid: null,
    taskDirectories: [],
    changedPaths: [],
  };
  const add = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({
      reasonCode,
      path: boundedBookkeepingText(path || '', 300),
      message: boundedBookkeepingText(message, 500),
      disposition,
    });
  };
  const addAdvisory = (reasonCode, path, message) => {
    if (advisories.length >= MAX_BOOKKEEPING_ADVISORIES) {
      advisoriesDropped += 1;
      return;
    }
    advisories.push({
      reasonCode,
      path: boundedBookkeepingText(path || '', 300),
      message: boundedBookkeepingText(message, 500),
    });
  };

  try {
    const loadedConfig = loadBookkeepingConfig(rootDir, options.configPath);
    config = loadedConfig.config;
    for (const message of loadedConfig.failures) {
      add('validator_config_invalid', '.sd-ai-command-pack/review-preflight.json', message);
    }
    if (options.command === 'pre-archive') {
      evidence.taskDirectories = [...new Set(options.taskDirs || [])].sort();
      for (const taskDir of evidence.taskDirectories) {
        validateBookkeepingTaskDirectory(taskDir, {
          add,
          archived: false,
          completionReady: true,
        });
      }
    } else if (options.command === 'seeded-task') {
      evidence.taskDirectories = [...new Set(options.taskDirs || [])].sort();
      for (const taskDir of evidence.taskDirectories) {
        // completionReady: false -- a task at checkout-validation legitimately
        // has no feature branch and is not yet in_progress/review.
        // seedReady: true -- and here the lone `_example` scaffold IS the defect;
        // see validateBookkeepingTaskContexts for why merge time exempts it.
        const record = validateBookkeepingTaskDirectory(taskDir, {
          add,
          archived: false,
          completionReady: false,
          seedReady: true,
        });
        validateSeededTaskBaseBranch(taskDir, record, evidence, add);
      }
    } else if (options.command === 'final-bundle') {
      validateBookkeepingFinalBundle(options, evidence, add, {}, addAdvisory);
    } else {
      add(
        'validator_command_invalid',
        '',
        'command must be pre-archive, seeded-task, or final-bundle',
      );
    }
  } catch (error) {
    add(
      'validator_internal_error',
      '',
      `bookkeeping validation could not complete: ${thrownValueMessage(error)}`,
      'indeterminate',
    );
  }

  if (advisoriesDropped > 0) {
    evidence.advisoriesDropped = advisoriesDropped;
  }
  const invalid = findings.some((finding) => finding.disposition === 'invalid');
  const status = invalid
    ? 'invalid'
    : findings.length > 0
      ? 'indeterminate'
      : 'valid';
  const validCode = options.command === 'pre-archive'
    ? 'pre_archive_valid'
    : options.command === 'seeded-task'
      ? 'seeded_task_valid'
      : `${options.mode || 'unknown'}_bundle_valid`;
  return {
    schemaVersion: BOOKKEEPING_SCHEMA_VERSION,
    kind: 'trellis-bookkeeping-validation',
    status,
    command: options.command || null,
    mode: options.command === 'final-bundle' ? options.mode : null,
    reasonCodes: status === 'valid'
      ? [validCode]
      : [...new Set(findings.map((finding) => finding.reasonCode))].sort(),
    evidence,
    findings,
    advisories,
  };
}

function loadBookkeepingConfig(root, configPath) {
  const previousFailures = failures;
  failures = [];
  try {
    const loaded = loadConfig(root, configPath);
    return { config: loaded, failures: [...failures] };
  } finally {
    failures = previousFailures;
  }
}

function boundedBookkeepingText(value, limit) {
  const repoPath = normalizePathSeparators(rootDir);
  let text = normalizePathSeparators(String(value ?? '')).replace(/[\r\n\t]+/g, ' ').trim();
  if (repoPath && repoPath !== '/') {
    text = text.split(repoPath).join('<repo>');
  }
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 3))}...`;
}

// Exported so the unknown-command branch is reachable from a test: the printer
// itself writes to the console and argv cannot produce an unrecognized command
// with a valid status. An earlier revision composed this by excluding
// final-bundle, which printed `null bundle undefined..undefined` for the then
// -new seeded-task; the next command added would have inherited a task count it
// never computed. Enumerating every command and throwing on the rest turns that
// silent wrong answer into a failure in whatever test first exercises it.
export function bookkeepingResultSubject(result) {
  if (result.command === 'final-bundle') {
    return `${result.mode} bundle ${result.evidence.baseOid?.slice(0, 12)}..${result.evidence.headOid?.slice(0, 12)}`;
  }
  if (result.command === 'pre-archive' || result.command === 'seeded-task') {
    return `${result.evidence.taskDirectories.length} task(s)`;
  }
  throw new Error(
    `bookkeeping result subject is undefined for command ${JSON.stringify(result.command)}; `
      + 'add it to bookkeepingResultSubject when adding the command',
  );
}

function printBookkeepingResult(result) {
  if (result.status === 'valid') {
    console.log(`PASS ${result.command} bookkeeping validation: ${bookkeepingResultSubject(result)}.`);
  } else {
    for (const finding of result.findings) {
      const location = finding.path ? ` ${finding.path}:` : '';
      console.log(`FAIL ${finding.reasonCode}${location} ${finding.message}`);
    }
  }
  const advisories = result.advisories || [];
  for (const advisory of advisories) {
    const location = advisory.path ? ` ${advisory.path}:` : '';
    console.log(`ADVISORY ${advisory.reasonCode}${location} ${advisory.message}`);
  }
  const dropped = result.evidence?.advisoriesDropped || 0;
  const advisorySuffix = advisories.length > 0 || dropped > 0
    ? `, ${advisories.length} advisory(ies)${dropped > 0 ? `, ${dropped} dropped over cap` : ''}`
    : '';
  console.log(`\nBookkeeping validator: ${result.status} (${result.findings.length} finding(s)${advisorySuffix}).`);
}

// The bookkeeping validator does not wire up the root-task base_branch rule --
// its only other call site is the merge-time preflight -- so seeded-task calls
// it directly rather than waiting for focused-candidate to reject the lane.
function validateSeededTaskBaseBranch(taskDir, record, evidence, add) {
  if (!isPlainObject(record)) {
    return;
  }

  const configured = (process.env.SD_AI_COMMAND_PACK_DEFAULT_BRANCH || '').trim();
  const defaultBranch = trellisRootDefaultBranchName();
  // Record the source, not just the value. Under --repo the environment
  // variable outranks the consumer's own origin/HEAD, so a wrong answer here
  // decides the one rule this gate exists to enforce; the receipt has to show
  // where the name came from.
  evidence.defaultBranch = defaultBranch || null;
  evidence.defaultBranchSource = configured
    ? 'SD_AI_COMMAND_PACK_DEFAULT_BRANCH'
    : defaultBranch
      ? 'origin/HEAD'
      : null;

  const taskFile = `${taskDir}/task.json`;
  if (!defaultBranch) {
    // Cannot tell, rather than wrong: neither the environment nor origin/HEAD
    // named a default branch, so the comparison is unavailable.
    add(
      'task_base_branch_indeterminate',
      taskFile,
      'repository default branch could not be resolved from SD_AI_COMMAND_PACK_DEFAULT_BRANCH '
        + 'or refs/remotes/origin/HEAD, so base_branch cannot be validated',
      'indeterminate',
    );
    return;
  }

  // The shared rule stays the single source of truth for *whether* this is a
  // defect; only the wording is stage-specific. Its own message offers
  // set-meta base_branch_exemption as the repair -- the escape hatch. Embedding
  // it here would put an exemption for the exact defect this stage exists to
  // catch ahead of the real fix, so the seeded-task finding states the mismatch
  // itself and recommends only set-base-branch.
  if (validateTrellisRootTaskBaseBranch(record, defaultBranch).length > 0) {
    add(
      'task_base_branch_invalid',
      taskFile,
      `field base_branch ${JSON.stringify(record.base_branch.trim())} must equal the repository `
        + `default branch ${JSON.stringify(defaultBranch.trim())}; repair with `
        + `python3 ./.trellis/scripts/task.py set-base-branch ${taskDir} ${defaultBranch.trim()} `
        + '-- run it immediately after task.py create, and do not use '
        + 'task.py create --base-branch, which the older vendored task_store.py '
        + 'rejects as an unrecognized argument',
    );
  }
}

function validateBookkeepingTaskDirectory(taskDir, options) {
  const { add, archived, completionReady = false, seedReady = false, addAdvisory = null, deltaPaths = null } = options;
  // Delta scoping: a defect anchored to a file inside the bundle delta blocks;
  // one anchored to an untouched file demotes to an advisory. Without a delta
  // set (pre-archive) or an advisory sink (historical replay), everything
  // blocks as before.
  const addScoped = (reasonCode, path, message, anchorPath = path) => {
    if (!deltaPaths || !addAdvisory || deltaPaths.has(anchorPath)) {
      add(reasonCode, path, message);
      return;
    }
    addAdvisory(reasonCode, path, message);
  };
  const expected = archived
    ? /^\.trellis\/tasks\/archive\/\d{4}-\d{2}\/\d{2}-\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*$/
    : /^\.trellis\/tasks\/\d{2}-\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*$/;
  if (!expected.test(taskDir)) {
    add('task_layout_invalid', taskDir, 'task directory is not in the supported mode-specific layout');
    return null;
  }
  const absolute = resolve(rootDir, taskDir);
  const relativeTaskDir = relative(rootDir, absolute);
  if (
    relativeTaskDir === '..'
    || relativeTaskDir.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`)
    || isAbsolute(relativeTaskDir)
  ) {
    add('task_path_outside_repository', taskDir, 'task directory resolves outside the repository');
    return null;
  }
  let directory;
  try {
    directory = lstatSync(absolute);
  } catch (error) {
    add('task_directory_unreadable', taskDir, `task directory could not be inspected: ${thrownValueMessage(error)}`);
    return null;
  }
  if (directory.isSymbolicLink() || !directory.isDirectory()) {
    add('task_directory_unsafe', taskDir, 'task directory must be a real directory, not a symlink or another file type');
    return null;
  }

  const taskFile = `${taskDir}/task.json`;
  const prdFile = `${taskDir}/prd.md`;
  const taskLoaded = loadTrellisTaskMetadataFile(taskFile);
  const prdLoaded = loadTrellisTaskPrdFile(prdFile);
  if (taskLoaded.status !== 'loaded') {
    addScoped('task_artifact_invalid', taskFile, taskLoaded.message);
  }
  if (prdLoaded.status !== 'loaded') {
    addScoped('task_prd_invalid', prdFile, prdLoaded.message);
  }
  // A failed task.json load or parse must not skip the sibling checks below:
  // with delta scoping that failure may be a mere advisory, and a
  // delta-changed prd.md or task-context file still needs its blocking
  // validation. Only the checks that read the parsed record are gated on it.
  let record = null;
  let recordAvailable = false;
  if (taskLoaded.status === 'loaded') {
    try {
      record = JSON.parse(taskLoaded.text);
      recordAvailable = true;
    } catch (error) {
      addScoped('task_json_invalid', taskFile, `task metadata is not valid JSON: ${thrownValueMessage(error)}`);
    }
  }
  if (recordAvailable) {
    for (const issue of validateTrellisBookkeepingMetadata(record, taskDir, archived)) {
      addScoped('task_metadata_invalid', taskFile, `field ${issue}`);
    }
    if (completionReady && !['in_progress', 'review'].includes(record.status)) {
      add('task_lifecycle_not_completion_ready', taskFile, 'status must be in_progress or review before archive');
    }
    if (completionReady && (typeof record.branch !== 'string' || record.branch.trim().length === 0)) {
      add('task_branch_invalid', taskFile, 'completion-ready task must have a non-empty feature branch');
    }
    if (archived && record.status !== 'completed') {
      addScoped('task_lifecycle_incomplete', taskFile, 'archived task status must be completed');
    }
  }
  if (prdLoaded.status === 'loaded') {
    if (prdLoaded.text.trim().length === 0) {
      addScoped('task_prd_empty', prdFile, 'task PRD must contain substantive content');
    }
    validateBookkeepingTextWhitespace(prdFile, prdLoaded.text, addScoped);
    for (const placeholder of findTrellisPlanningPlaceholders(prdFile, prdLoaded.text)) {
      // addScoped, not add: on a historical replay the same defect in an
      // untouched PRD demotes to an advisory instead of blocking a bundle whose
      // delta never went near it.
      addScoped(
        'task_prd_placeholder',
        prdFile,
        `line ${placeholder.line} still contains the generated placeholder ${JSON.stringify(placeholder.text)}`,
      );
    }
    if (completionReady && prdLoaded.text.trim().length > 0) {
      validateBookkeepingAcceptanceReadiness(prdFile, prdLoaded.text, add);
    }
  }
  if (taskLoaded.status === 'loaded') {
    validateBookkeepingTextWhitespace(taskFile, taskLoaded.text, addScoped);
  }
  validateBookkeepingTaskContexts(taskDir, record, archived, addScoped, seedReady);
  validateBookkeepingTopology(taskFile, taskDir, record, addScoped);
  return recordAvailable ? record : null;
}

function validateBookkeepingTaskContexts(taskDir, record, archived, add, seedReady = false) {
  // Context files are validated even when task.json is broken or missing —
  // their defects stand on their own. A manifest whose ONLY row is the untouched
  // generated `_example`-only scaffold `task.py create` writes is treated as
  // unfilled/advisory, not a blocking seed row — regardless of task status or
  // archival. A lone scaffold is indistinguishable from an empty/unfilled
  // manifest and is never genuine leftover scaffold; that only happens when an
  // `_example` row is MIXED with real rows, which still fails below. Gating the
  // exemption on `status === 'planning'` was too narrow and produced a LATE,
  // merge-time `task_context_seed` failure on completion (finding #5); the match
  // is on the lone-scaffold shape, not on Trellis's seed text.
  void record;
  void archived;
  for (const artifact of ['implement.jsonl', 'check.jsonl']) {
    const file = `${taskDir}/${artifact}`;
    if (!pathEntryExists(file)) continue;
    const loaded = loadBoundedTrellisTaskArtifact(file, 'task context');
    if (loaded.status !== 'loaded') {
      add('task_context_invalid', file, loaded.message);
      continue;
    }
    // seedReady flips the exemption off. At checkout-validation the ambiguity
    // that justifies it does not exist: the stage's whole purpose is to assert
    // the manifests were filled, so an unfilled manifest IS the defect.
    const exemptScaffold = !seedReady && isPristineTrellisTaskContextScaffold(loaded.text);
    let emittedForFile = 0;
    for (const issue of findTrellisTaskContextIssues(file, loaded.text)) {
      if (issue.kind === 'seed' && exemptScaffold) continue;
      emittedForFile += 1;
      const message = issue.kind === 'seed'
        ? `line ${issue.line} contains a generated _example scaffold row`
        : issue.kind === 'malformed'
          ? `line ${issue.line} is not valid JSONL`
          : issue.kind === 'self_reference'
            // Same repair string the merge-time check prints, so a seeding
            // operator reading only the receipt still gets the alternatives.
            ? `line ${issue.line} ${TRELLIS_TASK_CONTEXT_SELF_REFERENCE_REPAIR}`
            : `line ${issue.line} contains a reference outside the allowed spec/research roots`;
      add(`task_context_${issue.kind}`, file, message);
    }
    // Only at seeding time, and only when nothing else already named a defect
    // in this file. At merge time an unfilled manifest is indistinguishable
    // from one that was never curated -- the same ambiguity that justifies the
    // scaffold exemption above -- and failing it produced a late,
    // completion-time failure. At checkout-validation the stage's whole purpose
    // is to assert the manifests were filled, so unfilled IS the defect.
    //
    // The emittedForFile guard keeps the receipt precise: a lone scaffold, a
    // malformed line, and a self-citation each already say exactly what to fix,
    // and stacking a vaguer "no rows" finding on top would make it worse.
    //
    // The whitespace sweep runs BEFORE that decision and counts toward the same
    // guard. A manifest emptied to blank lines padded with spaces or tabs has
    // zero usable rows AND trailing whitespace, so ordering it after would
    // double-report exactly the shape most likely to occur.
    validateBookkeepingTextWhitespace(file, loaded.text, (reasonCode, path, message) => {
      emittedForFile += 1;
      add(reasonCode, path, message);
    });
    if (seedReady && emittedForFile === 0 && countTrellisTaskContextRows(loaded.text) === 0) {
      add(
        'task_context_unfilled',
        file,
        'contains no context rows; add at least one '
          + '{"file": "<spec-or-research-path>", "reason": "<why>"} row',
      );
    }
  }
}

function validateBookkeepingTopology(taskFile, taskDir, record, add) {
  if (!isPlainObject(record)) return;
  const taskName = taskDir.slice(taskDir.lastIndexOf('/') + 1);
  const loadReference = (field, name) => {
    const located = locateTrellisTaskRecord(name);
    if (located.error) {
      add('task_topology_unverifiable', taskFile, `${field} ${name} cannot be verified: ${located.error}`);
      return null;
    }
    if (located.paths.length !== 1) {
      add(
        located.paths.length === 0 ? 'task_topology_missing' : 'task_topology_ambiguous',
        taskFile,
        `${field} ${name} resolves to ${located.paths.length} task records`,
      );
      return null;
    }
    const loaded = loadTrellisTaskMetadataFile(located.paths[0]);
    if (loaded.status !== 'loaded') {
      // Reported at the neighbor's path, but scope-anchored to this task's own
      // task.json — the link under validation lives there.
      add('task_topology_unverifiable', located.paths[0], loaded.message, taskFile);
      return null;
    }
    try {
      return JSON.parse(loaded.text);
    } catch (error) {
      add('task_topology_unverifiable', located.paths[0], `linked task JSON is invalid: ${thrownValueMessage(error)}`, taskFile);
      return null;
    }
  };

  if (isTrellisTaskDirectoryName(record.parent)) {
    const parent = loadReference('parent', record.parent);
    if (parent && (!Array.isArray(parent.children) || !parent.children.includes(taskName))) {
      add('task_topology_not_reciprocal', taskFile, `parent ${record.parent} does not list ${taskName} as a child`);
    }
    if (parent) {
      for (const issue of validateTrellisPlanningBaseInheritance(record, parent)) {
        add('task_topology_base_invalid', taskFile, `field ${issue}`);
      }
    }
  }
  if (Array.isArray(record.children)) {
    for (const childName of new Set(record.children.filter(isTrellisTaskDirectoryName))) {
      const child = loadReference('child', childName);
      if (child && child.parent !== taskName) {
        add('task_topology_not_reciprocal', taskFile, `child ${childName} does not point back to ${taskName}`);
      }
    }
    if (record.children.length > 0) {
      const prd = loadTrellisTaskPrdFile(`${taskDir}/prd.md`);
      if (prd.status === 'loaded') {
        for (const child of findMissingTrellisChildReferences(prd.text, record.children)) {
          add('task_topology_prd_missing_child', `${taskDir}/prd.md`, `declared child ${child} is not represented in the PRD`);
        }
      }
    }
  }
}

function validateBookkeepingTextWhitespace(file, text, add) {
  const lines = text.split(/\n/);
  lines.forEach((line, index) => {
    const value = line.endsWith('\r') ? line.slice(0, -1) : line;
    if (/[ \t]+$/.test(value)) {
      add('bookkeeping_whitespace_invalid', file, `line ${index + 1} has trailing whitespace`);
    }
  });
}

function stripBookkeepingLineEnding(line) {
  return line.endsWith('\r') ? line.slice(0, -1) : line;
}

function normalizeBookkeepingHeadingText(text) {
  return text.trim().toLowerCase().replace(/[ \t]+/g, ' ');
}

// Mark every line that lives inside a fenced code block so Markdown examples of
// acceptance checkboxes or lifecycle headings (common in tooling PRDs) are not
// mistaken for the task's own criteria. Fences open and close on ``` or ~~~ of
// matching kind and at least the opening length.
function computeBookkeepingCodeFenceMask(lines) {
  const mask = new Array(lines.length).fill(false);
  let fence = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = stripBookkeepingLineEnding(lines[index]);
    const fenceMatch = CODE_FENCE_RE.exec(line);
    if (fence) {
      mask[index] = true;
      if (fenceMatch && fenceMatch[1][0] === fence[0] && fenceMatch[1].length >= fence.length) {
        fence = null;
      }
      continue;
    }
    if (fenceMatch) {
      fence = fenceMatch[1];
      mask[index] = true;
    }
  }
  return mask;
}

// Locate the canonical lifecycle sections in a bounded PRD. Each span covers the
// body lines (start inclusive, end exclusive) between a canonical heading and
// the next heading of equal-or-higher rank. Deterministic and read-only.
function collectBookkeepingLifecycleSections(lines, codeMask) {
  const headings = [];
  for (let index = 0; index < lines.length; index += 1) {
    if (codeMask[index]) continue;
    const match = ATX_HEADING_RE.exec(stripBookkeepingLineEnding(lines[index]));
    if (match) {
      headings.push({
        index,
        level: match[1].length,
        text: normalizeBookkeepingHeadingText(match[2]),
      });
    }
  }
  const spans = { acceptance: [], postArchive: [] };
  headings.forEach((heading, position) => {
    let key = null;
    if (heading.text === CANONICAL_ACCEPTANCE_HEADING) key = 'acceptance';
    else if (heading.text === CANONICAL_POST_ARCHIVE_HEADING) key = 'postArchive';
    if (!key) return;
    let end = lines.length;
    for (let next = position + 1; next < headings.length; next += 1) {
      if (headings[next].level <= heading.level) {
        end = headings[next].index;
        break;
      }
    }
    spans[key].push({ start: heading.index + 1, end });
  });
  return spans;
}

// Grade the checkbox list items inside the acceptance-criteria body. Prose
// bullets, continuation lines, and fenced code are ignored; only checkbox-form
// items are graded. Bounded so a pathological PRD cannot exhaust memory.
function scanBookkeepingAcceptanceItems(lines, span, codeMask) {
  const unchecked = [];
  const malformed = [];
  let scanned = 0;
  for (let index = span.start; index < span.end; index += 1) {
    if (scanned >= MAX_BOOKKEEPING_ACCEPTANCE_ITEMS) break;
    if (codeMask[index]) continue;
    const item = ACCEPTANCE_LIST_ITEM_RE.exec(stripBookkeepingLineEnding(lines[index]));
    if (!item || item[1].charAt(0) !== '[') continue;
    scanned += 1;
    const checkbox = ACCEPTANCE_CHECKBOX_RE.exec(item[1]);
    if (!checkbox) {
      malformed.push({ line: index + 1, reason: 'uses invalid acceptance checkbox syntax' });
      continue;
    }
    const marker = checkbox[1];
    const rest = checkbox[2];
    if (rest.length > 0 && !/^[ \t]/.test(rest)) {
      malformed.push({ line: index + 1, reason: 'is missing the space after its checkbox' });
      continue;
    }
    if (marker !== ' ') continue;
    if (rest.trim().length === 0) {
      malformed.push({ line: index + 1, reason: 'is an unchecked criterion with no description' });
    } else {
      unchecked.push(index + 1);
    }
  }
  return { unchecked, malformed };
}

// Enforce the completion-versus-housekeeping lifecycle contract at the
// pre-archive boundary: every required acceptance criterion must be checked
// before Trellis archives the task, and post-archive obligations must be prose
// so they are never mistaken for incomplete criteria. Absence of the canonical
// section is intentionally permissive; lightweight and pre-contract PRDs still
// pass. Read-only: it never rewrites the PRD or checks a box.
function validateBookkeepingAcceptanceReadiness(prdFile, text, add) {
  const lines = text.split('\n');
  const codeMask = computeBookkeepingCodeFenceMask(lines);
  const spans = collectBookkeepingLifecycleSections(lines, codeMask);

  if (spans.acceptance.length > 1) {
    add(
      'pre_archive_acceptance_malformed',
      prdFile,
      `PRD declares ${spans.acceptance.length} "Acceptance Criteria" sections; the completion contract allows exactly one`,
    );
  }
  if (spans.postArchive.length > 1) {
    add(
      'pre_archive_acceptance_malformed',
      prdFile,
      `PRD declares ${spans.postArchive.length} "Post-archive handoff" sections; the completion contract allows exactly one`,
    );
  }

  if (spans.acceptance.length === 1) {
    const { unchecked, malformed } = scanBookkeepingAcceptanceItems(
      lines,
      spans.acceptance[0],
      codeMask,
    );
    for (const offender of malformed.slice(0, MAX_BOOKKEEPING_ACCEPTANCE_FINDINGS)) {
      add(
        'pre_archive_acceptance_malformed',
        prdFile,
        `acceptance criteria line ${offender.line} ${offender.reason}`,
      );
    }
    if (unchecked.length > 0) {
      const preview = unchecked.slice(0, MAX_BOOKKEEPING_ACCEPTANCE_FINDINGS).join(', ');
      add(
        'pre_archive_acceptance_incomplete',
        prdFile,
        `acceptance criteria retain ${unchecked.length} unchecked required item(s) at line(s) ${preview}; every criterion must be satisfied and checked before archive`,
      );
    }
  }

  for (const span of spans.postArchive) {
    let flagged = 0;
    for (
      let index = span.start;
      index < span.end && flagged < MAX_BOOKKEEPING_ACCEPTANCE_FINDINGS;
      index += 1
    ) {
      if (codeMask[index]) continue;
      if (POST_ARCHIVE_CHECKBOX_RE.test(stripBookkeepingLineEnding(lines[index]))) {
        flagged += 1;
        add(
          'pre_archive_acceptance_malformed',
          prdFile,
          `post-archive handoff line ${index + 1} uses an acceptance checkbox; downstream obligations must be prose bullets, not criteria`,
        );
      }
    }
  }
}

function validateBookkeepingFinalBundle(options, evidence, add, runtime = {}, addAdvisory = null) {
  const baseOid = resolveBookkeepingCommit(options.base, 'base', add);
  const headOid = resolveBookkeepingCommit(options.head, 'head', add);
  evidence.baseOid = baseOid;
  evidence.headOid = headOid;
  if (!baseOid || !headOid) return;

  if (!runtime.historical) {
    const checkedOutHead = gitStdout(['rev-parse', '--verify', 'HEAD^{commit}']);
    if (checkedOutHead !== headOid) {
      add('bundle_head_not_checked_out', '', 'the requested final head must be the currently checked-out HEAD', 'indeterminate');
      return;
    }
  }
  for (const diffArgs of [
    ['diff', '--quiet', 'HEAD', '--', '.trellis/tasks', '.trellis/workspace'],
    ['diff', '--quiet', '--cached', '--', '.trellis/tasks', '.trellis/workspace'],
  ]) {
    const dirty = runGit(diffArgs);
    if (dirty.status !== 0) {
      add('bundle_worktree_dirty', '', 'task or workspace bookkeeping differs from the requested committed head', 'indeterminate');
      return;
    }
  }

  const entries = bookkeepingChangedEntries(baseOid, headOid, add);
  if (!entries) return;
  evidence.repository = bookkeepingRepositoryEvidence();
  const paths = [...new Set(entries.flatMap((entry) => [entry.oldPath, entry.path].filter(Boolean)))].sort();
  evidence.changedPaths = paths
    .slice(0, MAX_BOOKKEEPING_CHANGED_PATHS)
    .map((path) => boundedBookkeepingText(path, 300));
  if (paths.length > MAX_BOOKKEEPING_CHANGED_PATHS) {
    add('bundle_changed_paths_oversized', '', `bundle changes more than ${MAX_BOOKKEEPING_CHANGED_PATHS} paths`);
    return;
  }

  if (
    options.mode === 'completion'
    && entries.length === 0
    && runtime.allowCompletionSuccessor !== false
  ) {
    validateCompletionSuccessorRecovery(evidence, headOid, add);
    return;
  }

  const deltaPaths = new Set(paths);
  const unsupported = paths.filter(
    (path) => !path.startsWith('.trellis/tasks/') && !path.startsWith('.trellis/workspace/'),
  );
  for (const path of unsupported) {
    add('bundle_scope_invalid', path, 'finalization delta contains a non-bookkeeping path');
  }
  for (const entry of entries) {
    if (entry.status.startsWith('D')) continue;
    if (entry.mode !== '100644') {
      add(
        'bundle_unsupported_file_mode',
        entry.path,
        `finalization delta introduces unsupported file mode ${entry.mode}; only regular non-executable files are allowed`,
      );
    }
  }
  validateBookkeepingDiffWhitespace(baseOid, headOid, add);
  const journalSummary = validateBookkeepingJournalBundle(entries, baseOid, headOid, add);
  evidence.journalSessions = bookkeepingJournalSessionEvidence(journalSummary);
  if (options.mode === 'completion') {
    validateCompletionBundle(entries, evidence, baseOid, add, { addAdvisory, deltaPaths });
  } else {
    const taskEntries = bookkeepingTaskEntries(entries);
    if (taskEntries.length > 0) {
      validatePlanningBundle(entries, evidence, baseOid, add, { addAdvisory, deltaPaths });
    } else {
      validateJournalOnlyPlanningRecovery(entries, journalSummary, evidence, baseOid, add);
    }
  }
}

function bookkeepingRepositoryEvidence() {
  const branch = gitStdout(['symbolic-ref', '--quiet', '--short', 'HEAD']);
  const roots = runGit(['rev-list', '--max-parents=0', 'HEAD']);
  if (roots.status !== 0) {
    throw new GitCommandError('git rev-list could not determine repository lineage');
  }
  const rootOids = roots.stdout
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .sort();
  if (
    rootOids.length === 0
    || rootOids.some((oid) => !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(oid))
  ) {
    throw new GitCommandError('git rev-list returned invalid repository lineage');
  }
  const lineageHash = createHash('sha256').update('git-root-oids-v1\0');
  for (const oid of rootOids) {
    lineageHash.update(oid).update('\0');
  }
  return {
    branch: branch || null,
    lineageDigest: `sha256:${lineageHash.digest('hex')}`,
  };
}

function bookkeepingJournalSessionEvidence(summary) {
  return (summary?.newCompletedSessions || []).map((session) => ({
    file: session.file,
    number: session.number,
    commits: session.resolvedCommits.map((commit) => commit.oid),
  }));
}

// Mechanical extraction of the pre-existing archive-anchor search into a
// local-findings attempt function (implement.md step 3): "existing logic,
// local findings, no behavior change" in isolation. Also exposes
// `shapedTailCount` (an existing local variable) in the return value, which
// the orchestrator below needs to pick the right findings on double failure
// -- see the Round 3 note in design.md for why this exact field is the
// discriminator, found empirically by running the existing test suite
// against a literal implementation of an earlier, unconditional version of
// that orchestration (9 of 11 existing fixtures broke).
function attemptArchiveAnchorRecovery(headOid) {
  const findings = [];
  const add = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({ reasonCode, path, message, disposition });
  };
  const historyArgs = [
    'rev-list',
    '--first-parent',
    `--max-count=${MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS + 1}`,
    headOid,
  ];
  const history = runGit(historyArgs);
  if (history.status !== 0) {
    add(
      'completion_successor_history_unavailable',
      '',
      `Git could not enumerate bounded first-parent history for completion recovery${gitFailureSuffix(historyArgs, history.status, history.stderr)}`,
      'indeterminate',
    );
    return { status: 'indeterminate', shapedTailCount: 0, findings, evidence: {} };
  }
  const commits = history.stdout.trim().split(/\s+/).filter(Boolean);

  const eligible = [];
  let shapedTailCount = 0;
  let nearestAnchorFailure = null;
  for (let index = 0; index + 2 < commits.length; index += 1) {
    const bookkeepingHeadOid = commits[index];
    const archiveOid = commits[index + 1];
    const baseOid = commits[index + 2];
    const journalEntries = bookkeepingChangedEntries(archiveOid, bookkeepingHeadOid, () => {});
    if (journalEntries === null) {
      add(
        'completion_successor_history_unavailable',
        '',
        describeGitFailure('Git could not inspect a candidate journal delta during completion recovery'),
        'indeterminate',
      );
      return { status: 'indeterminate', shapedTailCount, findings, evidence: {} };
    }
    const archiveEntries = bookkeepingChangedEntries(baseOid, archiveOid, () => {});
    if (archiveEntries === null) {
      add(
        'completion_successor_history_unavailable',
        '',
        describeGitFailure('Git could not inspect a candidate archive delta during completion recovery'),
        'indeterminate',
      );
      return { status: 'indeterminate', shapedTailCount, findings, evidence: {} };
    }
    if (!isAdjacentJournalCommit(journalEntries) || !isAdjacentArchiveCommit(archiveEntries)) {
      continue;
    }
    shapedTailCount += 1;
    const successor = evaluateCompletionSuccessorRange(bookkeepingHeadOid, headOid);
    if (successor.status !== 'valid') {
      if (successor.status === 'invalid') {
        const anchorMoveSet = archiveMoveSet(archiveEntries);
        const revertedNames = completionAnchorRevertedNames(anchorMoveSet, successor.entries);
        for (const name of revertedNames) {
          add(
            'completion_successor_anchor_reverted',
            `.trellis/tasks/${name}`,
            `the completion anchor at ${archiveOid.slice(0, 12)} archives ${name}, but a later commit restores it to .trellis/tasks/${name}; the finish-work receipt no longer describes this head — re-run finish-work to regenerate it`,
          );
        }
      }
      for (const finding of successor.findings) {
        add(finding.reasonCode, finding.path, finding.message, finding.disposition);
      }
      return { status: 'invalid', shapedTailCount, findings, evidence: {} };
    }
    const anchor = evaluateHistoricalCompletionBundle(baseOid, bookkeepingHeadOid);
    if (anchor.status !== 'valid') {
      nearestAnchorFailure = anchor;
      break;
    }
    eligible.push({ anchor, successor });
    break;
  }
  if (eligible.length === 0) {
    if (shapedTailCount > 0 && nearestAnchorFailure) {
      const reasons = [...new Set(nearestAnchorFailure.findings.map((finding) => finding.reasonCode))]
        .slice(0, 8)
        .join(', ');
      add(
        'completion_successor_anchor_invalid',
        '',
        `the nearest adjacent archive/journal tail failed canonical completion validation${reasons ? `: ${reasons}` : ''}`,
      );
      return { status: 'invalid', shapedTailCount, findings, evidence: {} };
    }
    if (commits.length > MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS) {
      add(
        'completion_successor_history_oversized',
        '',
        `no completion anchor was found within ${MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS} first-parent commits`,
      );
      return { status: 'invalid', shapedTailCount, findings, evidence: {} };
    }
    add(
      'completion_successor_anchor_missing',
      '',
      'no bounded adjacent archive/journal completion tail is reachable from the final head',
    );
    return { status: 'invalid', shapedTailCount, findings, evidence: {} };
  }

  const selected = eligible[0];
  return {
    status: 'valid',
    shapedTailCount,
    findings,
    evidence: {
      taskDirectories: [...selected.anchor.evidence.taskDirectories],
      journalSessions: [...selected.anchor.evidence.journalSessions],
      completionAnchor: {
        source: 'historical-adjacent-tail',
        baseOid: selected.anchor.evidence.baseOid,
        bookkeepingHeadOid: selected.anchor.evidence.headOid,
        taskDirectories: [...selected.anchor.evidence.taskDirectories],
        journalSessions: [...selected.anchor.evidence.journalSessions],
      },
      successor: selected.successor.evidence,
    },
  };
}

function commitArchiveAnchorEvidence(evidence, archiveResult) {
  evidence.completionSubtype = 'post-archive-review-successor';
  evidence.taskDirectories = archiveResult.evidence.taskDirectories;
  evidence.journalSessions = archiveResult.evidence.journalSessions;
  evidence.completionAnchor = archiveResult.evidence.completionAnchor;
  evidence.successor = archiveResult.evidence.successor;
}

function commitActiveTaskAnchorEvidence(evidence, activeTaskResult) {
  evidence.completionSubtype = 'active-task-review-successor';
  evidence.taskDirectories = activeTaskResult.evidence.taskDirectories;
  evidence.completionAnchor = activeTaskResult.evidence.completionAnchor;
}

// Change B, step 1: discover "the" active task purely from live head content.
// Any candidate whose task.json fails to load/parse counts toward ambiguity,
// not toward "not a candidate" -- a malformed sibling record must not hide a
// genuine second in_progress/review task.
function discoverActiveTrellisTaskDirectory() {
  const tasksRoot = '.trellis/tasks';
  let entries;
  try {
    entries = readdirSync(resolve(rootDir, tasksRoot), { withFileTypes: true });
  } catch {
    return { taskDir: null, ambiguous: true };
  }
  const qualifying = [];
  let anyLoadFailure = false;
  for (const entry of entries) {
    if (entry.name === 'archive') continue;
    if (!/^\d{2}-\d{2}-[^/]+$/.test(entry.name)) continue;
    const taskDir = `${tasksRoot}/${entry.name}`;
    const loaded = loadTrellisTaskMetadataFile(`${taskDir}/task.json`);
    if (loaded.status !== 'loaded') {
      anyLoadFailure = true;
      continue;
    }
    let record;
    try {
      record = JSON.parse(loaded.text);
    } catch {
      anyLoadFailure = true;
      continue;
    }
    if (!isPlainObject(record)) {
      anyLoadFailure = true;
      continue;
    }
    if (record.status === 'in_progress' || record.status === 'review') {
      qualifying.push(taskDir);
    }
  }
  if (anyLoadFailure || qualifying.length !== 1) {
    return { taskDir: null, ambiguous: true };
  }
  return { taskDir: qualifying[0], ambiguous: false };
}

// Change B, step 2: find the range's starting point. Walks the bounded
// first-parent history from the newest fetched commit toward the oldest
// (indices commits.length-2 down to 0 -- commits[commits.length] is out of
// bounds and there is no commit further back to serve as a parent), looking
// for the OLDEST commit that (a) touches the task's own directory and (b)
// whose PARENT is already a qualifying in_progress/review record. Condition
// (b) is load-bearing: `task.py create` (status planning) and `task.py
// start` (planning -> in_progress) both touch the task's own directory too,
// and neither's own preceding state can satisfy IN_PLACE_IDENTITY_OPTIONS's
// sourceStatuses -- without requiring the parent to already qualify, the
// search would select one of those commits as the starting point for any
// task whose whole lifecycle fits in the search window, which is the
// ordinary case for a young task, not an edge case.
function findActiveTaskHistoricalBase(taskDir, headOid) {
  const historyArgs = [
    'rev-list',
    '--first-parent',
    `--max-count=${MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS + 1}`,
    headOid,
  ];
  const history = runGit(historyArgs);
  if (history.status !== 0) {
    return {
      status: 'indeterminate',
      findings: [
        {
          reasonCode: 'completion_successor_history_unavailable',
          path: '',
          message: `Git could not enumerate bounded first-parent history for active-task completion recovery${gitFailureSuffix(historyArgs, history.status, history.stderr)}`,
          disposition: 'indeterminate',
        },
      ],
    };
  }
  const commits = history.stdout.trim().split(/\s+/).filter(Boolean);

  let historicalBase = null;
  let qualifyingIndex = null;
  for (let i = commits.length - 2; i >= 0; i -= 1) {
    const entries = bookkeepingChangedEntries(commits[i + 1], commits[i], () => {});
    if (entries === null) {
      return {
        status: 'indeterminate',
        findings: [
          {
            reasonCode: 'completion_successor_history_unavailable',
            path: '',
            message: describeGitFailure('Git could not inspect a candidate task-directory delta during active-task completion recovery'),
            disposition: 'indeterminate',
          },
        ],
      };
    }
    const touchesTaskDir = entries.some((entry) =>
      [entry.oldPath, entry.path].filter(Boolean).some((path) => path.startsWith(`${taskDir}/`)));
    if (!touchesTaskDir) continue;
    // Shape probe, not a validation step -- mirrors isAdjacentJournalCommit's
    // silent-probe pattern. Full lifecycle/identity rigor happens later, once
    // historicalBase is chosen, with real `add` reporting.
    const probed = loadBookkeepingJsonAtRef(commits[i + 1], `${taskDir}/task.json`, () => {});
    if (probed && (probed.status === 'in_progress' || probed.status === 'review')) {
      historicalBase = commits[i + 1];
      qualifyingIndex = i;
      break;
    }
  }

  if (historicalBase === null) {
    return {
      status: 'invalid',
      findings: [
        {
          reasonCode: 'completion_successor_active_task_anchor_missing',
          path: '',
          message: 'no commit touching the active task directory within the bounded search window resolves to a qualifying starting point',
          disposition: 'invalid',
        },
      ],
    };
  }
  // The window cannot distinguish "this is really where the task's
  // bookkeeping starts" from "the task's history continues past what was
  // fetched" when the fetch hit its cap and the only qualifying candidate is
  // right at the edge of the fetched window.
  if (commits.length > MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS && qualifyingIndex === commits.length - 2) {
    return {
      status: 'invalid',
      findings: [
        {
          reasonCode: 'completion_successor_history_oversized',
          path: '',
          message: `no active-task completion anchor was confidently resolved within ${MAX_BOOKKEEPING_ANCHOR_SEARCH_COMMITS} first-parent commits`,
          disposition: 'invalid',
        },
      ],
    };
  }
  return { status: 'valid', historicalBase };
}

// Change B, steps 3+4: validate the whole historicalBase..headOid range as a
// unit -- bound + per-commit linearity (a merge commit anywhere in the range
// is caught here automatically, including if the "oldest touching" commit
// itself is one), plus two orthogonal scope checks that neither substitutes
// for the other: an aggregate net-unique-path count (reuses the existing
// dedup pattern exactly), and a per-commit category check that inspects
// every path in every commit's OWN diff (catches a forbidden mutation later
// reverted by another commit in the same range, which a net diff would
// miss).
function evaluateActiveTaskSuccessorRange(taskDir, historicalBase, headOid) {
  const findings = [];
  const add = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({ reasonCode, path, message, disposition });
  };
  const rangeArgs = ['rev-list', '--first-parent', '--reverse', `${historicalBase}..${headOid}`];
  const range = runGit(rangeArgs);
  if (range.status !== 0) {
    add(
      'completion_successor_history_unavailable',
      '',
      `Git could not inspect the active-task completion-successor commit range${gitFailureSuffix(rangeArgs, range.status, range.stderr)}`,
      'indeterminate',
    );
    return { status: 'indeterminate', findings };
  }
  const commits = range.stdout.trim().split(/\s+/).filter(Boolean);
  if (commits.length > MAX_BOOKKEEPING_SUCCESSOR_COMMITS) {
    add(
      'completion_successor_history_oversized',
      '',
      `active-task completion successor contains more than ${MAX_BOOKKEEPING_SUCCESSOR_COMMITS} commits`,
    );
  }

  let parent = historicalBase;
  for (const oid of commits.slice(0, MAX_BOOKKEEPING_SUCCESSOR_COMMITS)) {
    const parents = runGit(['rev-list', '--parents', '-n', '1', oid]);
    const fields = parents.status === 0
      ? parents.stdout.trim().split(/\s+/).filter(Boolean)
      : [];
    if (fields.length !== 2 || fields[0] !== oid) {
      add(
        'completion_successor_history_non_linear',
        '',
        `active-task completion successor commit ${oid.slice(0, 12)} must have exactly one parent`,
        parents.status === 0 ? 'invalid' : 'indeterminate',
      );
      parent = oid;
      continue;
    }
    const commitEntries = bookkeepingChangedEntries(parent, oid, () => {});
    if (commitEntries === null) {
      add(
        'completion_successor_history_unavailable',
        '',
        describeGitFailure(`Git could not inspect the per-commit delta for active-task completion successor commit ${oid.slice(0, 12)}`),
        'indeterminate',
      );
      return { status: 'indeterminate', findings };
    }
    for (const entry of commitEntries) {
      for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
        // Ordinary code (not under .trellis/ and not finalization runtime
        // evidence) is always allowed; the task's own directory is allowed;
        // a journal/index workspace file is allowed. Anything else --
        // another task, the archive, .trellis/.runtime/,
        // .sd-ai-command-pack/finish-work, a non-journal workspace path --
        // is forbidden.
        const allowed =
          (!path.startsWith('.trellis/') && !path.startsWith('.sd-ai-command-pack/finish-work'))
          || path.startsWith(`${taskDir}/`)
          || /^\.trellis\/workspace\/[^/]+\/(?:journal-\d+\.md|index\.md)$/.test(path);
        if (!allowed) {
          add(
            'completion_successor_scope_invalid',
            path,
            'active-task completion successor must not change another task, the archive, runtime evidence, or a non-journal workspace path',
          );
        }
      }
    }
    parent = oid;
  }

  const entries = bookkeepingChangedEntries(historicalBase, headOid, () => {});
  if (entries === null) {
    add(
      'completion_successor_history_unavailable',
      '',
      describeGitFailure('Git could not inspect changed paths in the active-task completion-successor range'),
      'indeterminate',
    );
    return { status: 'indeterminate', findings };
  }
  const paths = new Set(entries.flatMap((entry) => [entry.oldPath, entry.path].filter(Boolean)));
  if (paths.size > MAX_BOOKKEEPING_CHANGED_PATHS) {
    add(
      'completion_successor_scope_oversized',
      '',
      `active-task completion successor changes more than ${MAX_BOOKKEEPING_CHANGED_PATHS} paths`,
    );
  }

  const invalid = findings.some((finding) => finding.disposition === 'invalid');
  return {
    status: invalid ? 'invalid' : findings.length > 0 ? 'indeterminate' : 'valid',
    findings,
  };
}

function attemptActiveTaskAnchorRecovery(headOid) {
  const discovery = discoverActiveTrellisTaskDirectory();
  if (discovery.ambiguous) {
    return {
      status: 'invalid',
      shapedTailCount: 0,
      findings: [
        {
          reasonCode: 'completion_successor_active_task_ambiguous',
          path: '',
          message: 'zero, more than one, or an unreadable in_progress/review task candidate exists at head; the active-task successor cannot be attempted',
          disposition: 'invalid',
        },
      ],
      evidence: {},
    };
  }
  const taskDir = discovery.taskDir;

  const base = findActiveTaskHistoricalBase(taskDir, headOid);
  if (base.status !== 'valid') {
    return { status: base.status, shapedTailCount: 0, findings: base.findings, evidence: {} };
  }
  const historicalBase = base.historicalBase;

  const range = evaluateActiveTaskSuccessorRange(taskDir, historicalBase, headOid);
  if (range.status !== 'valid') {
    return { status: range.status, shapedTailCount: 0, findings: range.findings, evidence: {} };
  }

  // Steps 5-7: identity, journal presence, and the full content sweep all
  // accumulate into one shared findings list -- these are three independent
  // properties, not a sequential dependency chain, so all three run and
  // report together rather than stopping at the first one.
  const findings = [];
  const add = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({ reasonCode, path, message, disposition });
  };

  const taskFile = `${taskDir}/task.json`;
  // Full content sweep, once, live -- deltaPaths: null means every defect in
  // the task directory's current content blocks, matching Change A's direct
  // path and the pre-archive gate; addAdvisory is irrelevant here since a
  // null deltaPaths already routes every finding through `add`, never
  // addAdvisory. Its return value is also step 5's "current" record, so the
  // sweep runs exactly once, not once per historical checkpoint.
  const current = validateBookkeepingTaskDirectory(taskDir, {
    add,
    archived: false,
    addAdvisory: null,
    deltaPaths: null,
  });
  // historicalBase is a genuine git-at-ref read; current is genuinely live
  // head, never a stand-in for some earlier point.
  const source = loadBookkeepingJsonAtRef(historicalBase, taskFile, add);
  validateTaskLifecycleIdentity(source, current, taskFile, taskFile, add, IN_PLACE_IDENTITY_OPTIONS);

  const rangeEntries = bookkeepingChangedEntries(historicalBase, headOid, add);
  if (rangeEntries !== null) {
    validateBookkeepingJournalBundle(rangeEntries, historicalBase, headOid, add);
  }

  const invalid = findings.some((finding) => finding.disposition === 'invalid');
  const status = invalid ? 'invalid' : findings.length > 0 ? 'indeterminate' : 'valid';
  if (status !== 'valid') {
    return { status, shapedTailCount: 0, findings, evidence: {} };
  }
  return {
    status: 'valid',
    shapedTailCount: 0,
    findings,
    evidence: {
      taskDirectories: [taskDir],
      completionAnchor: {
        source: 'active-task-range',
        taskDir,
        historicalBase,
        headOid,
      },
    },
  };
}

function validateCompletionSuccessorRecovery(evidence, headOid, add) {
  const archiveResult = attemptArchiveAnchorRecovery(headOid);
  if (archiveResult.status === 'valid') {
    commitArchiveAnchorEvidence(evidence, archiveResult);
    return;
  }
  const activeTaskResult = attemptActiveTaskAnchorRecovery(headOid);
  if (activeTaskResult.status === 'valid') {
    commitActiveTaskAnchorEvidence(evidence, activeTaskResult);
    return;
  }
  // Both failed. Prefer the archive search's OWN diagnosis when it found a
  // real shaped archive/journal tail that failed downstream (a specific,
  // actionable reason already exists there), or when Git itself failed
  // mid-search (`status === 'indeterminate'` -- a materially more urgent
  // situation than "genuinely found nothing," reusing the three-way
  // valid/invalid/indeterminate convention `evaluateCompletionSuccessorRange`
  // already uses elsewhere in this file). Only defer to the active-task
  // diagnosis when the archive search definitively found no shaped tail at
  // all anywhere in bounded history -- see design.md's Control Flow
  // "Round 3 note" for the full empirical trace of both fixes.
  const findingsToCommit = (archiveResult.shapedTailCount > 0 || archiveResult.status === 'indeterminate')
    ? archiveResult.findings
    : activeTaskResult.findings;
  for (const f of findingsToCommit) add(f.reasonCode, f.path, f.message, f.disposition);
}

function isAdjacentJournalCommit(entries) {
  if (!entries || entries.length === 0) return false;
  let hasJournal = false;
  let hasIndex = false;
  for (const entry of entries) {
    if (entry.status.startsWith('D') || entry.status.startsWith('R') || entry.status.startsWith('C')) {
      return false;
    }
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (!/^\.trellis\/workspace\/[^/]+\/(?:journal-\d+\.md|index\.md)$/.test(path)) {
        return false;
      }
      hasJournal ||= /\/journal-\d+\.md$/.test(path);
      hasIndex ||= path.endsWith('/index.md');
    }
  }
  return hasJournal && hasIndex;
}

function archiveTaskName(path) {
  return ARCHIVE_TASK_JSON.exec(path)?.[1] ?? null;
}

function activeTaskName(path) {
  return ACTIVE_TASK_JSON.exec(path)?.[1] ?? null;
}

// The commit's true archive move-set: task names that both land in archive/ as a
// non-D destination AND vacate their active location (rename source of an R… entry,
// or the path of a D entry). git diff runs --find-renames with no --find-copies, so
// C… is never emitted here; the intersection is what distinguishes a real archive
// from a pure un-archive, and C3 reuses it so an unrelated archive copy in the same
// commit is never mistaken for the anchored task.
function archiveMoveSet(entries) {
  if (!entries || entries.length === 0) return new Set();
  const archivedNames = new Set();
  const vacatedNames = new Set();
  for (const entry of entries) {
    if (!entry.status.startsWith('D')) {
      const name = archiveTaskName(entry.path);
      if (name) archivedNames.add(name);
    }
    if (entry.status.startsWith('R') && entry.oldPath) {
      const name = activeTaskName(entry.oldPath);
      if (name) vacatedNames.add(name);
    }
    if (entry.status.startsWith('D')) {
      const name = activeTaskName(entry.path);
      if (name) vacatedNames.add(name);
    }
  }
  const moveSet = new Set();
  for (const name of archivedNames) {
    if (vacatedNames.has(name)) moveSet.add(name);
  }
  return moveSet;
}

function isAdjacentArchiveCommit(entries) {
  if (!entries || entries.length === 0) return false;
  const paths = entries.flatMap((entry) => [entry.oldPath, entry.path].filter(Boolean));
  if (paths.some((path) => !path.startsWith('.trellis/tasks/'))) return false;
  return archiveMoveSet(entries).size > 0;
}

// Names in the anchor's archive move-set that a later commit genuinely un-archived:
// both halves required for the same name — the archived task.json leaves (rename
// source of an R… entry, or the path of a D entry) AND the active task.json arrives
// (non-D destination). An AND, not an OR: either half alone is a different event
// (cleanup, or a duplicate copy) and keeps its own scope code.
function completionAnchorRevertedNames(anchorMoveSet, successorEntries) {
  const reverted = new Set();
  if (!anchorMoveSet || anchorMoveSet.size === 0) return reverted;
  if (!successorEntries || successorEntries.length === 0) return reverted;
  const archiveLeft = new Set();
  const activeArrived = new Set();
  for (const entry of successorEntries) {
    if (entry.status.startsWith('R') && entry.oldPath) {
      const name = archiveTaskName(entry.oldPath);
      if (name) archiveLeft.add(name);
    }
    if (entry.status.startsWith('D')) {
      const name = archiveTaskName(entry.path);
      if (name) archiveLeft.add(name);
    }
    if (!entry.status.startsWith('D')) {
      const name = activeTaskName(entry.path);
      if (name) activeArrived.add(name);
    }
  }
  for (const name of anchorMoveSet) {
    if (archiveLeft.has(name) && activeArrived.has(name)) reverted.add(name);
  }
  return reverted;
}

function evaluateHistoricalCompletionBundle(baseOid, headOid) {
  const findings = [];
  const localAdd = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({ reasonCode, path, message, disposition });
  };
  const localEvidence = {
    baseOid: null,
    headOid: null,
    taskDirectories: [],
    changedPaths: [],
  };
  validateBookkeepingFinalBundle(
    { command: 'final-bundle', mode: 'completion', base: baseOid, head: headOid },
    localEvidence,
    localAdd,
    { historical: true, allowCompletionSuccessor: false },
  );
  return {
    status: findings.length === 0 ? 'valid' : 'invalid',
    evidence: localEvidence,
    findings,
  };
}

function evaluateCompletionSuccessorRange(anchorOid, headOid) {
  const findings = [];
  const add = (reasonCode, path, message, disposition = 'invalid') => {
    if (findings.length >= MAX_BOOKKEEPING_FINDINGS) return;
    findings.push({ reasonCode, path, message, disposition });
  };
  const rangeArgs = ['rev-list', '--first-parent', '--reverse', `${anchorOid}..${headOid}`];
  const range = runGit(rangeArgs);
  if (range.status !== 0) {
    add(
      'completion_successor_history_unavailable',
      '',
      `Git could not inspect the completion-successor commit range${gitFailureSuffix(rangeArgs, range.status, range.stderr)}`,
      'indeterminate',
    );
    return { status: 'indeterminate', evidence: {}, findings };
  }
  const commits = range.stdout.trim().split(/\s+/).filter(Boolean);
  if (commits.length > MAX_BOOKKEEPING_SUCCESSOR_COMMITS) {
    add(
      'completion_successor_history_oversized',
      '',
      `completion successor contains more than ${MAX_BOOKKEEPING_SUCCESSOR_COMMITS} commits`,
    );
  }
  const commitEvidence = [];
  for (const oid of commits.slice(0, MAX_BOOKKEEPING_SUCCESSOR_COMMITS)) {
    const parents = runGit(['rev-list', '--parents', '-n', '1', oid]);
    const fields = parents.status === 0
      ? parents.stdout.trim().split(/\s+/).filter(Boolean)
      : [];
    if (fields.length !== 2 || fields[0] !== oid) {
      add(
        'completion_successor_history_non_linear',
        '',
        `successor commit ${oid.slice(0, 12)} must have exactly one parent`,
        parents.status === 0 ? 'invalid' : 'indeterminate',
      );
      continue;
    }
    const subjectArgs = ['log', '-1', '--format=%s', oid];
    const subjectResult = runGit(subjectArgs);
    if (subjectResult.status !== 0) {
      add(
        'completion_successor_history_unavailable',
        '',
        `Git could not inspect the subject for successor commit ${oid.slice(0, 12)}${gitFailureSuffix(subjectArgs, subjectResult.status, subjectResult.stderr)}`,
        'indeterminate',
      );
      return { status: 'indeterminate', evidence: {}, findings };
    }
    const subject = subjectResult.stdout.trim();
    commitEvidence.push({
      oid,
      subjectDigest: `sha256:${createHash('sha256').update(subject).digest('hex')}`,
    });
  }

  const entries = bookkeepingChangedEntries(anchorOid, headOid, () => {});
  if (entries === null) {
    add(
      'completion_successor_history_unavailable',
      '',
      describeGitFailure('Git could not inspect changed paths in the completion-successor range'),
      'indeterminate',
    );
    return { status: 'indeterminate', evidence: {}, findings };
  }
  const paths = [...new Set(
    entries.flatMap((entry) => [entry.oldPath, entry.path].filter(Boolean)),
  )].sort();
  if (paths.length > MAX_BOOKKEEPING_CHANGED_PATHS) {
    add(
      'completion_successor_scope_oversized',
      '',
      `completion successor changes more than ${MAX_BOOKKEEPING_CHANGED_PATHS} paths`,
    );
  }
  for (const path of paths.slice(0, MAX_BOOKKEEPING_CHANGED_PATHS)) {
    if (
      path.startsWith('.trellis/tasks/')
      || path.startsWith('.trellis/workspace/')
      || path.startsWith('.trellis/.runtime/')
      || path.startsWith('.sd-ai-command-pack/finish-work')
    ) {
      add(
        'completion_successor_scope_invalid',
        path,
        'completion successor must not change task, workspace, or finalization runtime evidence',
      );
    }
  }
  const invalid = findings.some((finding) => finding.disposition === 'invalid');
  return {
    status: invalid ? 'invalid' : findings.length > 0 ? 'indeterminate' : 'valid',
    evidence: {
      anchorOid,
      headOid,
      commits: commitEvidence,
      changedPaths: paths.slice(0, MAX_BOOKKEEPING_CHANGED_PATHS),
    },
    entries,
    findings,
  };
}

function resolveBookkeepingCommit(ref, label, add) {
  if (typeof ref !== 'string' || ref.length > 255 || ref.startsWith('-') || /[\s\0]/.test(ref)) {
    add('bundle_git_ref_invalid', '', `${label} ref is not a bounded Git commit expression`);
    return null;
  }
  const result = runGit(['rev-parse', '--verify', `${ref}^{commit}`]);
  if (result.status !== 0) {
    add('bundle_git_ref_unknown', '', `${label} ref does not resolve to a known commit`, 'indeterminate');
    return null;
  }
  return result.stdout.trim();
}

function bookkeepingChangedEntries(baseOid, headOid, add) {
  lastBookkeepingGitFailure = null;
  const diffArgs = ['diff', '--raw', '-z', '--find-renames', baseOid, headOid, '--'];
  const result = runGit(diffArgs);
  if (result.status !== 0) {
    lastBookkeepingGitFailure = { commandArgs: diffArgs, status: result.status, stderr: result.stderr };
    add('bundle_diff_unavailable', '', describeGitFailure('Git could not enumerate the finalization delta'), 'indeterminate');
    return null;
  }
  const tokens = result.stdout.split('\0');
  const entries = [];
  for (let index = 0; index < tokens.length && tokens[index];) {
    // Raw metadata token for a two-endpoint diff:
    // ":<srcmode> <dstmode> <srcsha> <dstsha> <status>". Capturing the
    // destination mode lets the bundle validators reject executable, symlink,
    // and gitlink/submodule entries that name-status alone cannot distinguish.
    const meta = /^:(\d{6}) (\d{6}) [0-9a-f]+ [0-9a-f]+ ([A-Z]\d*)$/.exec(tokens[index++]);
    if (!meta) {
      add('bundle_diff_malformed', '', 'Git returned a malformed raw diff record', 'indeterminate');
      return null;
    }
    const srcMode = meta[1];
    const mode = meta[2];
    const status = meta[3];
    if (/^[RC]\d+$/.test(status)) {
      const oldPath = tokens[index++];
      const path = tokens[index++];
      if (!oldPath || !path) {
        add('bundle_diff_malformed', '', 'Git returned a malformed rename/copy record', 'indeterminate');
        return null;
      }
      entries.push({ status, oldPath, path, srcMode, mode });
    } else {
      const path = tokens[index++];
      if (!/^[AMDUT]$/.test(status) || !path) {
        add('bundle_diff_malformed', '', 'Git returned an unsupported or malformed path record', 'indeterminate');
        return null;
      }
      entries.push({ status, oldPath: '', path, srcMode, mode });
    }
  }
  return entries;
}

function validateBookkeepingDiffWhitespace(baseOid, headOid, add) {
  const checkArgs = ['diff', '--check', baseOid, headOid, '--', '.trellis/tasks', '.trellis/workspace'];
  const result = runGit(checkArgs);
  if (result.status === 0) return;
  const detail = (result.stdout || result.stderr).trim();
  if (!detail) {
    add('bundle_whitespace_unavailable', '', `Git whitespace validation could not complete${gitFailureSuffix(checkArgs, result.status, result.stderr)}`, 'indeterminate');
    return;
  }
  for (const line of detail.split(/\r?\n/).slice(0, MAX_BOOKKEEPING_FINDINGS)) {
    add('bookkeeping_whitespace_invalid', '', line);
  }
}

// Validates the status/completedAt/field-identity invariant shared by the
// archive-move and in-place completion-bundle shapes. Behavior differs by
// caller because `task.py archive` unconditionally rewrites a fresh archive
// record (status -> completed, completedAt set) while an in-place touch must
// keep both fields exactly as the base recorded them; parameterize rather
// than hardcode one shape so each caller only sees the checks it needs.
// `sourcePath`/`currentPath` are separate because the archive-move caller
// reports the source-status finding at the active task's path but the
// identity-changed finding at the archived path -- a single shared path
// cannot reproduce that split. The in-place caller passes the same path
// twice (the file never moves; only the ref differs).
function validateTaskLifecycleIdentity(source, current, sourcePath, currentPath, add, options) {
  const {
    sourceStatuses,
    checkCurrentStatus = false,
    currentStatuses,
    requireStatusEqual = false,
    checkSourceCompletedAtNull = false,
    checkCompletedAt = false,
    currentCompletedAtRule,
    tolerateBranchNewlyRecorded = false,
    sourceCode = 'completion_source_lifecycle_invalid',
    identityCode = 'completion_task_identity_changed',
  } = options;
  if (!source || !current) return;
  if (!sourceStatuses.includes(source.status)) {
    add(sourceCode, sourcePath, `source status must be one of: ${sourceStatuses.join(', ')}`);
  }
  if (checkSourceCompletedAtNull && source.completedAt !== null) {
    add(sourceCode, sourcePath, 'source completedAt must be null for this bundle shape');
  }
  if (checkCurrentStatus && !currentStatuses.includes(current.status)) {
    add(sourceCode, currentPath, `status must be one of: ${currentStatuses.join(', ')}`);
  }
  if (requireStatusEqual && source.status !== current.status) {
    add(sourceCode, currentPath, 'status must stay unchanged for this bundle shape');
  }
  if (checkCompletedAt) {
    const completedAtOk = currentCompletedAtRule === 'null'
      ? current.completedAt === null
      : typeof current.completedAt === 'string' && current.completedAt.trim().length > 0;
    if (!completedAtOk) {
      add(
        identityCode,
        currentPath,
        `completedAt must be ${currentCompletedAtRule === 'null' ? 'null' : 'a non-empty timestamp'} for this bundle shape`,
      );
    }
  }
  const stripLifecycle = (record) => {
    const copy = { ...record };
    delete copy.status;
    delete copy.completedAt;
    return copy;
  };
  const sourceRecord = stripLifecycle(source);
  const currentRecord = stripLifecycle(current);
  // The pre-archive gate requires a completion-ready task to carry a
  // non-empty branch. An operator who satisfies it after the finalization
  // base is captured lands that write inside the archive commit, where this
  // comparison would otherwise read it as smuggled content. Archive-move
  // tolerates exactly that transition -- a rewrite or an erasure still fails,
  // and an absent key is a distinct state that stays blocked. The in-place
  // shape never tolerates it (Decision 4): status and branch must be
  // byte-identical between base and head.
  const branchNewlyRecorded = tolerateBranchNewlyRecorded
    && sourceRecord.branch === null
    && typeof currentRecord.branch === 'string'
    && currentRecord.branch.trim().length > 0;
  if (branchNewlyRecorded) {
    delete sourceRecord.branch;
    delete currentRecord.branch;
  }
  if (stableJson(sourceRecord) !== stableJson(currentRecord)) {
    add(identityCode, currentPath, 'fields other than status, completedAt, and (where tolerated) a newly recorded branch changed');
  }
}

function validateCompletionBundle(entries, evidence, baseOid, add, options = {}) {
  const taskEntries = entries.filter((entry) =>
    entry.path.startsWith('.trellis/tasks/') || entry.oldPath.startsWith('.trellis/tasks/'));
  const mappings = [];
  for (const entry of taskEntries) {
    if (!entry.path.endsWith('/task.json')) continue;
    const source = entry.oldPath || '';
    if (
      /^\.trellis\/tasks\/\d{2}-\d{2}-[^/]+\/task\.json$/.test(source) &&
      /^\.trellis\/tasks\/archive\/\d{4}-\d{2}\/\d{2}-\d{2}-[^/]+\/task\.json$/.test(entry.path) &&
      source.split('/').at(-2) === entry.path.split('/').at(-2)
    ) {
      mappings.push({ sourceDir: dirname(source), archiveDir: dirname(entry.path) });
    }
  }
  const deletedTaskFiles = taskEntries
    .filter((entry) => entry.status === 'D' && /^\.trellis\/tasks\/\d{2}-\d{2}-[^/]+\/task\.json$/.test(entry.path))
    .map((entry) => entry.path);
  const addedTaskFiles = taskEntries
    .filter((entry) => entry.status === 'A' && /^\.trellis\/tasks\/archive\/\d{4}-\d{2}\/\d{2}-\d{2}-[^/]+\/task\.json$/.test(entry.path))
    .map((entry) => entry.path);
  for (const source of deletedTaskFiles) {
    const destination = addedTaskFiles.find(
      (candidate) => source.split('/').at(-2) === candidate.split('/').at(-2),
    );
    if (destination) {
      mappings.push({ sourceDir: dirname(source), archiveDir: dirname(destination) });
    }
  }
  const uniqueMappings = [...new Map(
    mappings.map((mapping) => [`${mapping.sourceDir}\0${mapping.archiveDir}`, mapping]),
  ).values()];
  if (uniqueMappings.length === 0) {
    const inPlace = detectInPlaceTaskTouch(taskEntries);
    if (inPlace) {
      validateInPlaceTaskTouch(inPlace.taskDir, taskEntries, baseOid, add, options, evidence);
      return;
    }
    add(
      'completion_archive_move_missing',
      '',
      'completion bundle must move an active task into an archive month, or touch exactly one active task directory in place',
    );
    return;
  }
  evidence.taskDirectories = uniqueMappings.map((mapping) => mapping.archiveDir).sort();
  const allowedPrefixes = uniqueMappings.flatMap((mapping) => [`${mapping.sourceDir}/`, `${mapping.archiveDir}/`]);
  for (const entry of taskEntries) {
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (!allowedPrefixes.some((prefix) => path.startsWith(prefix))) {
        add('completion_task_scope_invalid', path, 'task change is outside the detected archive move set');
      }
    }
  }

  for (const mapping of uniqueMappings) {
    const source = loadBookkeepingJsonAtRef(baseOid, `${mapping.sourceDir}/task.json`, add);
    const archived = validateBookkeepingTaskDirectory(mapping.archiveDir, {
      add,
      archived: true,
      addAdvisory: options.addAdvisory ?? null,
      deltaPaths: options.deltaPaths ?? null,
    });
    if (!source || !archived) continue;
    for (const issue of validateTrellisBookkeepingMetadata(source, mapping.sourceDir, false)) {
      add('completion_source_metadata_invalid', `${mapping.sourceDir}/task.json`, `field ${issue}`);
    }
    validateTaskLifecycleIdentity(
      source,
      archived,
      `${mapping.sourceDir}/task.json`,
      `${mapping.archiveDir}/task.json`,
      add,
      ARCHIVE_MOVE_IDENTITY_OPTIONS,
    );
  }
}

// Second valid completion-bundle shape (Change A): an in_progress/review
// task's own directory touched in place, with no archive move at all. Only
// reachable when no clean archive-move mapping was detected. Collects the set
// of active (non-archive) task directories touched by `taskEntries`, using
// the same extraction `validatePlanningBundle` uses for its own task-dir set.
// Any `archive/`-prefixed path here means a malformed/partial archive attempt
// -- fall through to the caller's existing `completion_archive_move_missing`
// message rather than treating it as an in-place touch. Exactly one
// non-archive directory and zero archive-prefixed paths qualifies; zero or
// more than one falls through to that same existing failure.
function detectInPlaceTaskTouch(taskEntries) {
  const activeDirs = new Set();
  let hasArchivePath = false;
  for (const entry of taskEntries) {
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (path.startsWith('.trellis/tasks/archive/')) {
        hasArchivePath = true;
        continue;
      }
      const match = /^(\.trellis\/tasks\/\d{2}-\d{2}-[^/]+)\//.exec(path);
      if (match) activeDirs.add(match[1]);
    }
  }
  if (hasArchivePath || activeDirs.size !== 1) return null;
  return { taskDir: [...activeDirs][0] };
}

function validateInPlaceTaskTouch(taskDir, taskEntries, baseOid, add, options, evidence) {
  // completion_task_scope_invalid covers both shapes for this bundle: a
  // task-entry path outside the one detected directory, and a delete/rename
  // inside it -- mirroring the archive-move shape's own allowedPrefixes sweep
  // plus its (absence of a) deletion tolerance.
  for (const entry of taskEntries) {
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (!path.startsWith(`${taskDir}/`)) {
        add('completion_task_scope_invalid', path, 'task change is outside the detected in-place task directory');
      }
    }
    if (entry.status.startsWith('D') || entry.status.startsWith('R')) {
      add('completion_task_scope_invalid', entry.oldPath || entry.path, 'in-place task touch must not delete or rename task artifacts');
    }
  }
  const current = validateBookkeepingTaskDirectory(taskDir, {
    add,
    archived: false,
    addAdvisory: options.addAdvisory ?? null,
    deltaPaths: options.deltaPaths ?? null,
  });
  const source = loadBookkeepingJsonAtRef(baseOid, `${taskDir}/task.json`, add);
  validateTaskLifecycleIdentity(
    source,
    current,
    `${taskDir}/task.json`,
    `${taskDir}/task.json`,
    add,
    IN_PLACE_IDENTITY_OPTIONS,
  );
  evidence.taskDirectories = [taskDir];
}

function bookkeepingTaskEntries(entries) {
  return entries.filter((entry) =>
    entry.path.startsWith('.trellis/tasks/') || entry.oldPath.startsWith('.trellis/tasks/'));
}

function validatePlanningBundle(entries, evidence, baseOid, add, options = {}) {
  const taskEntries = bookkeepingTaskEntries(entries);
  const taskDirs = new Set();
  for (const entry of taskEntries) {
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (path.startsWith('.trellis/tasks/archive/')) {
        add('planning_archive_mutation', path, 'planning bundle must not mutate archived tasks');
      }
      const match = /^(\.trellis\/tasks\/\d{2}-\d{2}-[^/]+)\//.exec(path);
      if (!match) {
        add('planning_task_layout_invalid', path, 'planning task change must remain in an active supported task directory');
      } else {
        taskDirs.add(match[1]);
      }
    }
    if (entry.status.startsWith('D') || entry.status.startsWith('R')) {
      add('planning_task_deletion', entry.oldPath || entry.path, 'planning bundle must not delete or move task artifacts');
    }
  }
  if (taskDirs.size === 0) {
    add('planning_task_change_missing', '', 'planning bundle must include at least one active task artifact change');
  }
  evidence.taskDirectories = [...taskDirs].sort();
  const changedNames = new Set(
    evidence.taskDirectories.map((taskDir) => taskDir.slice(taskDir.lastIndexOf('/') + 1)),
  );
  const changedRecords = [];
  for (const taskDir of evidence.taskDirectories) {
    const current = options.lifecycleOnly
      ? loadRecoveredPlanningTaskRecord(taskDir, add, options.currentRef)
      : validateBookkeepingTaskDirectory(taskDir, {
          add,
          archived: false,
          addAdvisory: options.addAdvisory ?? null,
          deltaPaths: options.deltaPaths ?? null,
        });
    if (!current) continue;
    changedRecords.push(current);
    if (current.status !== 'planning' || current.completedAt !== null || current.branch !== null) {
      add('planning_lifecycle_mutation', `${taskDir}/task.json`, 'planning task must keep status planning, completedAt null, and branch null');
    }
    const baselineOptions = options.lifecycleOnly
      ? {
          missingAllowed: true,
          artifactReasonPrefix: 'planning_recovery_commit_parent_artifact',
          refLabel: 'the recovered work commit parent',
          jsonReasonCode: 'planning_recovery_commit_parent_task_json_invalid',
        }
      : { missingAllowed: true };
    const baseline = loadBookkeepingJsonAtRef(baseOid, `${taskDir}/task.json`, add, baselineOptions);
    if (baseline && (baseline.status !== 'planning' || baseline.completedAt !== null || baseline.branch !== null)) {
      add('planning_baseline_invalid', `${taskDir}/task.json`, 'existing task was not a valid planning task at the bundle base');
    }
  }
  validatePlanningClosureActiveTasks(changedRecords, changedNames, add);
  return evidence.taskDirectories;
}

// A valid planning finalization preserves only planning tasks. When a changed
// planning task links (parent/child) to a task outside the changed set that is
// itself an active in_progress/review task, the finalization would step over
// in-flight implementation work, so it blocks with a stable reason.
function validatePlanningClosureActiveTasks(changedRecords, changedNames, add) {
  const inspected = new Set();
  for (const record of changedRecords) {
    if (!isPlainObject(record)) continue;
    const neighbors = [];
    if (isTrellisTaskDirectoryName(record.parent)) neighbors.push(record.parent);
    if (Array.isArray(record.children)) {
      for (const child of record.children) {
        if (isTrellisTaskDirectoryName(child)) neighbors.push(child);
      }
    }
    for (const name of neighbors) {
      if (changedNames.has(name) || inspected.has(name)) continue;
      inspected.add(name);
      const located = locateTrellisTaskRecord(name);
      if (located.error || located.paths.length !== 1) continue;
      const loaded = loadTrellisTaskMetadataFile(located.paths[0]);
      if (loaded.status !== 'loaded') continue;
      let neighbor;
      try {
        neighbor = JSON.parse(loaded.text);
      } catch {
        continue;
      }
      if (isPlainObject(neighbor) && (neighbor.status === 'in_progress' || neighbor.status === 'review')) {
        add(
          'planning_active_task_outside_closure',
          located.paths[0],
          `linked task ${name} is ${neighbor.status}; planning finalization must not leave an active task outside the changed planning closure`,
        );
      }
    }
  }
}

function loadRecoveredPlanningTaskRecord(taskDir, add, ref) {
  const taskFile = `${taskDir}/task.json`;
  if (ref) {
    return loadBookkeepingJsonAtRef(ref, taskFile, add, {
      artifactReasonPrefix: 'planning_recovery_commit_artifact',
      refLabel: 'the recovered work commit',
      jsonReasonCode: 'planning_recovery_commit_task_json_invalid',
    });
  }
  const loaded = loadTrellisTaskMetadataFile(taskFile);
  if (loaded.status !== 'loaded') {
    add('task_artifact_invalid', taskFile, loaded.message);
    return null;
  }
  validateBookkeepingTextWhitespace(taskFile, loaded.text, add);
  try {
    const record = JSON.parse(loaded.text);
    if (!isPlainObject(record)) throw new Error('top-level value is not an object');
    return record;
  } catch (error) {
    add('task_json_invalid', taskFile, `task metadata is not valid JSON: ${thrownValueMessage(error)}`);
    return null;
  }
}

function validateBookkeepingJournalBundle(entries, baseOid, headOid, add) {
  const workspaceEntries = entries.filter((entry) =>
    entry.path.startsWith('.trellis/workspace/') || entry.oldPath.startsWith('.trellis/workspace/'));
  const journalFiles = new Set();
  const developerDirs = new Set();
  for (const entry of workspaceEntries) {
    if (entry.status.startsWith('D') || entry.status.startsWith('R') || entry.status.startsWith('C')) {
      add(
        'journal_history_mutated',
        entry.oldPath || entry.path,
        'journal and index history is append-or-update only; deletion, rename, and copy are not allowed',
      );
    }
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      const match = /^(\.trellis\/workspace\/[^/]+)\/(journal-\d+\.md|index\.md)$/.exec(path);
      if (!match) {
        add('journal_scope_invalid', path, 'finalization may change only journal-N.md and its sibling index.md');
        continue;
      }
      developerDirs.add(match[1]);
      if (match[2].startsWith('journal-')) journalFiles.add(path);
    }
  }
  if (journalFiles.size === 0 || developerDirs.size === 0) {
    add('journal_session_missing', '', 'finalization bundle must include a completed journal session and sibling index update');
    return { newCompletedSessions: [] };
  }

  const newCompletedSessions = [];
  for (const developerRelative of [...developerDirs].sort()) {
    const indexFile = `${developerRelative}/index.md`;
    if (!workspaceEntries.some((entry) => entry.path === indexFile || entry.oldPath === indexFile)) {
      add('journal_index_missing', indexFile, 'changed journal directory must include its sibling index.md');
      continue;
    }
    const currentJournalFiles = safeJournalFiles(developerRelative, add);
    const journalSessions = [];
    const baselineJournalSessions = [];
    for (const file of currentJournalFiles) {
      const loaded = loadBoundedTrellisTaskArtifact(file, 'journal');
      if (loaded.status !== 'loaded') {
        add('journal_artifact_invalid', file, loaded.message);
        continue;
      }
      validateBookkeepingTextWhitespace(file, loaded.text, add);
      const current = parseJournalSessionsFromText(file, loaded.text);
      journalSessions.push(...current);
      const baselineText = loadBookkeepingTextAtRef(baseOid, file, add, { missingAllowed: true });
      const baseline = baselineText === null ? [] : parseJournalSessionsFromText(file, baselineText);
      baselineJournalSessions.push(...baseline);
      const baselineByNumber = new Map(baseline.map((session) => [session.number, session]));
      for (const session of current) {
        const previous = baselineByNumber.get(session.number);
        if (previous && normalizeJournalSessionContent(previous.content) === normalizeJournalSessionContent(session.content)) {
          continue;
        }
        if (!session.completed) continue;
        newCompletedSessions.push({
          ...session,
          resolvedCommits: validateNewBookkeepingSession(session, headOid, add),
        });
      }
    }
    const indexLoaded = loadBoundedTrellisTaskArtifact(indexFile, 'journal index');
    let indexSessions = null;
    if (indexLoaded.status !== 'loaded') {
      add('journal_index_invalid', indexFile, indexLoaded.message);
    } else {
      validateBookkeepingTextWhitespace(indexFile, indexLoaded.text, add);
      indexSessions = parseWorkspaceIndexSessionsFromText(indexFile, indexLoaded.text, {
        onDuplicate: (message) => add('journal_index_duplicate', indexFile, message),
      });
    }
    for (const issue of findHistoricalTrellisJournalSessionEdits(baselineJournalSessions, journalSessions)) {
      add('journal_history_mutated', issue.session.file, `Session ${issue.session.number} was ${issue.kind}`);
    }
    const validation = validateTrellisJournalSessions({
      baselineJournalSessions,
      developerRelative,
      indexFile,
      indexSessions,
      journalSessions,
    });
    for (const message of validation.failures) {
      add('journal_index_mismatch', indexFile, message);
    }
  }
  if (newCompletedSessions.length === 0) {
    add('journal_session_missing', '', 'finalization bundle adds no completed journal session');
  }
  return { newCompletedSessions };
}

function safeJournalFiles(developerRelative, add) {
  let entries;
  try {
    entries = readdirSync(resolve(rootDir, developerRelative), { withFileTypes: true });
  } catch (error) {
    add('journal_directory_unreadable', developerRelative, `journal directory could not be inspected: ${thrownValueMessage(error)}`);
    return [];
  }
  return entries
    .filter((entry) => entry.isFile() && /^journal-\d+\.md$/.test(entry.name))
    .map((entry) => `${developerRelative}/${entry.name}`)
    .sort((left, right) => left.localeCompare(right, undefined, { numeric: true }));
}

function validateNewBookkeepingSession(session, headOid, add) {
  const resolvedCommits = [];
  for (const heading of ['Summary', 'Main Changes', 'Testing']) {
    const section = extractMarkdownSection(session.content, heading)
      .replace(/^[\s*-]+|[\s*-]+$/g, '')
      .trim();
    if (
      section.length < 4 ||
      /\(Add (?:details|test results)\)/.test(section) ||
      /^(?:[-*]\s*)?(?:none|n\/?a|not recorded|see git log)[.!]?$/i.test(section)
    ) {
      add('journal_content_missing', session.file, `Session ${session.number} ${heading} must contain real content`);
    }
  }
  if (session.commits.length === 0) {
    add('journal_commit_missing', session.file, `Session ${session.number} must reference at least one work commit`);
    return resolvedCommits;
  }
  for (const hash of session.commits) {
    const resolved = runGit(['rev-parse', '--verify', `${hash}^{commit}`]);
    if (resolved.status !== 0) {
      add('journal_commit_unknown', session.file, `Session ${session.number} references unknown commit ${hash}`);
      continue;
    }
    const oid = resolved.stdout.trim();
    resolvedCommits.push({ hash, oid });
    const ancestor = runGit(['merge-base', '--is-ancestor', oid, headOid]);
    if (ancestor.status !== 0) {
      add('journal_commit_unreachable', session.file, `Session ${session.number} commit ${hash} is not reachable from the final head`);
    }
  }
  return resolvedCommits;
}

function validateJournalOnlyPlanningRecovery(entries, journalSummary, evidence, baseOid, add) {
  const sessions = journalSummary?.newCompletedSessions || [];
  if (sessions.length !== 1) {
    add(
      'planning_recovery_session_count_invalid',
      '',
      `journal-only planning recovery requires exactly one newly completed session; found ${sessions.length}`,
    );
  }
  if (sessions.length === 0) {
    add('planning_recovery_task_change_missing', '', 'journal-only planning recovery proves no active task change');
    return;
  }
  if (sessions.length !== 1) return;

  const session = sessions[0];
  evidence.planningSubtype = 'journal-only-recovery';
  const allowedBundlePaths = new Set([session.file, `${dirname(session.file)}/index.md`]);
  for (const entry of entries) {
    for (const path of [entry.oldPath, entry.path].filter(Boolean)) {
      if (!allowedBundlePaths.has(path)) {
        add(
          'planning_recovery_bundle_scope_invalid',
          path,
          'journal-only planning recovery may contain only the new session journal and its sibling index',
        );
      }
    }
  }

  if (session.commits.length > MAX_BOOKKEEPING_RECOVERY_COMMITS) {
    add(
      'planning_recovery_commits_oversized',
      session.file,
      `journal-only planning recovery references more than ${MAX_BOOKKEEPING_RECOVERY_COMMITS} commits`,
    );
    add('planning_recovery_task_change_missing', '', 'journal-only planning recovery proves no bounded active task change');
    return;
  }

  const uniqueCommits = [];
  const seenCommits = new Set();
  for (const commit of session.resolvedCommits) {
    if (seenCommits.has(commit.oid)) {
      add(
        'planning_recovery_commit_duplicate',
        session.file,
        `Session ${session.number} resolves more than one commit reference to ${commit.oid.slice(0, 12)}`,
      );
      continue;
    }
    seenCommits.add(commit.oid);
    uniqueCommits.push(commit);
  }

  const recoveredTaskDirs = new Set();
  let allowedPathChanges = 0;
  for (const commit of uniqueCommits) {
    const published = runGit(['merge-base', '--is-ancestor', commit.oid, baseOid]);
    if (published.status !== 0) {
      add(
        'planning_recovery_commit_not_published',
        session.file,
        `Session ${session.number} commit ${commit.hash} is not an ancestor of the captured finalization base`,
        published.status === 1 ? 'invalid' : 'indeterminate',
      );
      continue;
    }

    const parentArgs = ['rev-list', '--parents', '-n', '1', commit.oid];
    const parentResult = runGit(parentArgs);
    if (parentResult.status !== 0) {
      add(
        'planning_recovery_commit_unavailable',
        session.file,
        `Git could not inspect parents for commit ${commit.hash}${gitFailureSuffix(parentArgs, parentResult.status, parentResult.stderr)}`,
        'indeterminate',
      );
      continue;
    }
    const parentFields = parentResult.stdout.trim().split(/\s+/).filter(Boolean);
    if (parentFields.length !== 2 || parentFields[0] !== commit.oid) {
      add(
        'planning_recovery_commit_non_linear',
        session.file,
        `Session ${session.number} commit ${commit.hash} must have exactly one parent`,
      );
      continue;
    }

    const commitEntries = bookkeepingChangedEntries(parentFields[1], commit.oid, add);
    if (!commitEntries) continue;
    const commitPaths = [...new Set(
      commitEntries.flatMap((entry) => [entry.oldPath, entry.path].filter(Boolean)),
    )];
    if (commitPaths.length > MAX_BOOKKEEPING_CHANGED_PATHS) {
      add(
        'planning_recovery_commit_scope_invalid',
        '',
        `commit ${commit.hash} changes more than ${MAX_BOOKKEEPING_CHANGED_PATHS} paths`,
      );
      continue;
    }
    const regularPaths = bookkeepingRegularPathsAtCommit(commit.oid, commitPaths);

    // Cited-commit paths partition five ways: the task archive, malformed
    // task-namespace paths, and workspace paths block; active-task paths keep
    // the current per-path and lifecycle rules; every other repository path is
    // allowed as ordinary maintenance work, including deletes and renames.
    const taskRelatedEntries = [];
    for (const entry of commitEntries) {
      const entryPaths = [entry.oldPath, entry.path].filter(Boolean);
      const taskRelated = entryPaths.some((path) => path.startsWith('.trellis/tasks/'));
      if (taskRelated) taskRelatedEntries.push(entry);
      const invalidOperation =
        entry.status.startsWith('D') || entry.status.startsWith('R') || entry.status.startsWith('C');
      if (taskRelated && invalidOperation) {
        add(
          'planning_recovery_commit_scope_invalid',
          entry.oldPath || entry.path,
          `commit ${commit.hash} deletes, renames, or copies a task artifact`,
        );
      }
      for (const path of entryPaths) {
        if (/[\0\r\n]/.test(path)) {
          add(
            'planning_recovery_commit_scope_invalid',
            path,
            `commit ${commit.hash} changes a path with unsupported control characters`,
          );
          continue;
        }
        if (path.startsWith('.trellis/tasks/archive/')) {
          add(
            'planning_recovery_commit_scope_invalid',
            path,
            `commit ${commit.hash} mutates the task archive`,
          );
          continue;
        }
        if (path.startsWith('.trellis/tasks/')) {
          const match = /^(\.trellis\/tasks\/\d{2}-\d{2}-[A-Za-z0-9][A-Za-z0-9._-]*)\/(.+)$/.exec(path);
          if (!match) {
            add(
              'planning_recovery_commit_scope_invalid',
              path,
              `commit ${commit.hash} changes a malformed task-namespace path`,
            );
            continue;
          }
          if (!invalidOperation && path === entry.path && !regularPaths.has(path)) {
            add(
              'planning_recovery_commit_scope_invalid',
              path,
              `commit ${commit.hash} does not leave a regular task artifact at this path`,
            );
            continue;
          }
          allowedPathChanges += 1;
          continue;
        }
        if (path.startsWith('.trellis/workspace/')) {
          add(
            'planning_recovery_commit_scope_invalid',
            path,
            `commit ${commit.hash} changes a workspace path, which journal-only recovery does not support`,
          );
          continue;
        }
        allowedPathChanges += 1;
      }
    }

    if (taskRelatedEntries.length > 0) {
      const commitEvidence = { taskDirectories: [] };
      for (const taskDir of validatePlanningBundle(
        taskRelatedEntries,
        commitEvidence,
        parentFields[1],
        add,
        { lifecycleOnly: true, currentRef: commit.oid },
      )) {
        recoveredTaskDirs.add(taskDir);
      }
    }
  }

  evidence.taskDirectories = [...recoveredTaskDirs].sort();
  if (allowedPathChanges === 0) {
    add('planning_recovery_task_change_missing', '', 'journal-only planning recovery cites no active-task or repository change');
  }
}

function bookkeepingRegularPathsAtCommit(commitOid, paths) {
  if (paths.length === 0) return new Set();
  const regularPaths = new Set();
  for (const batch of chunkBookkeepingGitPathspecs(paths)) {
    const result = runGit(['ls-tree', '-z', commitOid, '--', ...batch]);
    if (result.status !== 0) return new Set();
    for (const record of result.stdout.split('\0').filter(Boolean)) {
      const separator = record.indexOf('\t');
      if (separator <= 0) continue;
      const metadata = record.slice(0, separator);
      if (/^100644 blob [0-9a-f]{40,64}$/.test(metadata)) {
        regularPaths.add(record.slice(separator + 1));
      }
    }
  }
  return regularPaths;
}

function chunkBookkeepingGitPathspecs(paths) {
  const batches = [];
  let batch = [];
  let batchBytes = 0;
  for (const path of paths) {
    const pathBytes = Buffer.byteLength(path, 'utf8') + 1;
    if (pathBytes > MAX_BOOKKEEPING_GIT_PATHSPEC_BYTES) return [];
    if (batch.length > 0 && batchBytes + pathBytes > MAX_BOOKKEEPING_GIT_PATHSPEC_BYTES) {
      batches.push(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(path);
    batchBytes += pathBytes;
  }
  if (batch.length > 0) batches.push(batch);
  return batches;
}

function loadBookkeepingJsonAtRef(ref, file, add, options = {}) {
  const text = loadBookkeepingTextAtRef(ref, file, add, options);
  if (text === null) return null;
  try {
    const value = JSON.parse(text);
    if (!isPlainObject(value)) throw new Error('top-level value is not an object');
    return value;
  } catch (error) {
    add(
      options.jsonReasonCode || 'task_json_invalid',
      file,
      `task metadata at ${options.refLabel || 'the bundle base'} is invalid: ${thrownValueMessage(error)}`,
    );
    return null;
  }
}

function loadBookkeepingTextAtRef(ref, file, add, options = {}) {
  const artifactReasonPrefix = options.artifactReasonPrefix || 'bundle_base_artifact';
  const refLabel = options.refLabel || 'the bundle base';
  const size = runGit(['cat-file', '-s', `${ref}:${file}`]);
  if (size.status !== 0) {
    if (options.missingAllowed) return null;
    add(`${artifactReasonPrefix}_missing`, file, `artifact is missing from ${refLabel}`);
    return null;
  }
  const bytes = Number(size.stdout.trim());
  if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > config.untrackedFileReadLimitBytes) {
    add(
      `${artifactReasonPrefix}_oversized`,
      file,
      `artifact at ${refLabel} exceeds the bounded read limit of ${config.untrackedFileReadLimitBytes} bytes`,
    );
    return null;
  }
  const result = spawnSync('git', ['show', `${ref}:${file}`], {
    cwd: rootDir,
    encoding: 'buffer',
    maxBuffer: GIT_MAX_BUFFER_BYTES,
  });
  if (result.error || result.status !== 0 || result.signal) {
    add(
      `${artifactReasonPrefix}_unreadable`,
      file,
      `artifact could not be read from ${refLabel}`,
      'indeterminate',
    );
    return null;
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(result.stdout);
  } catch {
    add(`${artifactReasonPrefix}_utf8_invalid`, file, `artifact at ${refLabel} is not valid UTF-8`);
    return null;
  }
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (isPlainObject(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

export function unsupportedNodeVersionMessage(version) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)/.exec(version || '');
  if (!match) {
    return `sd-ai-command-pack-review-preflight.mjs requires Node >= ${MIN_NODE_VERSION.label}; could not parse ${version || 'unknown version'}.`;
  }

  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (major > MIN_NODE_VERSION.major || (major === MIN_NODE_VERSION.major && minor >= MIN_NODE_VERSION.minor)) {
    return '';
  }

  return `sd-ai-command-pack-review-preflight.mjs requires Node >= ${MIN_NODE_VERSION.label}; current ${version}.`;
}

function runCheck(label, check) {
  try {
    check();
  } catch (error) {
    if (error instanceof GitCommandError) {
      // A git invocation that could not run (missing binary, spawn or
      // buffer failure) must fail the preflight instead of letting the
      // check proceed with an empty diff.
      fail(`${label}: ${error.message}`);
      return;
    }
    const reason = thrownValueMessage(error);
    fail(`${label} check crashed: ${reason}`);
  }
}

export function thrownValueMessage(value) {
  return value instanceof Error ? value.message : String(value);
}

function checkPackageOverrides() {
  const file = 'package.json';
  if (!exists(file)) {
    pass(`${file} is not present; npm override check skipped.`);
    return;
  }

  const pkg = readJson(file);
  const overrides = pkg?.overrides;

  if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) {
    pass(`${file} does not define npm overrides.`);
    return;
  }

  const directOverrides = new Set();
  const scopedOverrides = new Map();

  for (const [selector, value] of Object.entries(overrides)) {
    if (isPlainObject(value)) {
      if (Object.prototype.hasOwnProperty.call(value, '.')) {
        directOverrides.add(packageNameFromOverrideSelector(selector));
      }

      collectNestedOverridePackages(value, [selector], scopedOverrides);
    } else {
      directOverrides.add(packageNameFromOverrideSelector(selector));
    }
  }

  const duplicates = [...scopedOverrides.entries()].filter(([packageName]) => directOverrides.has(packageName));

  if (duplicates.length > 0) {
    for (const [packageName, locations] of duplicates) {
      fail(`${file} defines "${packageName}" both globally and under ${locations.join(', ')}.`);
    }
    return;
  }

  pass(`${file} keeps dependency overrides in one source of truth per package.`);
}

function checkCopiedTemplateDiffDisclosure() {
  const diff = currentChangedPaths();

  if (diff === null) {
    warn('could not inspect current diff for copied Trellis/SD command-pack surfaces.');
    return;
  }

  if (diff.paths.length === 0) {
    pass('no current diff to inspect for copied Trellis/SD command-pack surfaces.');
    return;
  }

  const summary = summarizeCopiedTemplateDiff(diff.paths, {
    integrationPaths: config.integrationPaths,
  });

  if (summary.copied.length === 0) {
    pass(`${diff.label} does not change copied Trellis or SD command-pack surfaces.`);
    return;
  }

  if (summary.integration.length === 0) {
    warn(
      `${diff.label} changes copied Trellis/SD command-pack surfaces without companion repo-owned integration context: ` +
        `${summary.copied.slice(0, 6).join(', ')}${summary.copied.length > 6 ? ', ...' : ''}. ` +
        'Mention whether this is an upstream refresh or add a repo-owned integration note.',
    );
    return;
  }

  pass(
    `${diff.label} changes copied Trellis/SD command-pack surfaces with repo-owned integration context: ` +
      `${summary.integration.slice(0, 4).join(', ')}${summary.integration.length > 4 ? ', ...' : ''}.`,
  );
}

function checkDocumentationPathReferences() {
  const missing = [];

  for (const file of documentationGuardFiles()) {
    const basename = file.split('/').pop();
    if (
      file === 'docs/SD_AI_COMMAND_PACK.md' ||
      file === 'docs/repomix-map.md' ||
      file.startsWith('.trellis/tasks/archive/') ||
      // Design/implement artifacts are forward-looking: they reference files
      // the task proposes to CREATE, so a path-existence check is wrong for
      // them. PRDs/specs describe current state and keep the check.
      ((basename === 'design.md' || basename === 'implement.md') &&
        file.startsWith('.trellis/tasks/'))
    ) {
      continue;
    }

    const referenceText = maskGeneratedDocumentationPathProvenance(
      file,
      readText(file),
    );
    missing.push(
      ...findMissingDocumentationPathReferences(
        file,
        referenceText,
        (candidate) => exists(candidate),
      ),
    );
  }

  for (const reference of missing) {
    fail(`${reference.file}:${reference.line} references missing path ${reference.target}.`);
  }

  if (missing.length > 0) {
    return;
  }

  pass('documentation path references resolve to existing repo files or documented external/local-only paths.');
}

function checkDocumentationPathHygiene() {
  const failureStart = failures.length;
  const files = documentationGuardFiles();
  const personalPathPatterns = [
    { pattern: /\/Users\/([A-Za-z0-9._-]+)\//g, platform: 'macOS' },
    { pattern: /\/home\/([A-Za-z0-9._-]+)\//g, platform: 'Linux' },
    { pattern: /[A-Za-z]:\\{1,2}Users\\{1,2}([A-Za-z0-9._-]+)\\{1,2}/g, platform: 'Windows' },
  ];
  let scanned = 0;

  for (const file of files) {
    const text = readText(file);
    scanned += 1;

    for (const { pattern, platform } of personalPathPatterns) {
      for (const match of text.matchAll(pattern)) {
        const username = match[1] || '';
        if (platform === 'Linux' && config.allowedLinuxHomeUsers.includes(username)) {
          continue;
        }

        fail(`${file}:${lineNumberAt(text, match.index ?? 0)} includes a personal ${platform} absolute path; use repo-relative paths or a generic placeholder.`);
      }
    }
  }

  if (failures.length > failureStart) {
    return;
  }

  pass(`checked ${scanned} documentation/prompt/spec file(s) for personal absolute paths.`);
}

function checkChangedTrellisTaskMetadata() {
  const failureStart = failures.length;
  const diff = currentChangedPaths();
  if (diff === null) {
    warn('could not inspect current diff for changed Trellis task metadata.');
    return;
  }

  const taskFiles = new Set();
  for (const path of diff.paths) {
    const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
    if (normalized.startsWith('.trellis/tasks/') && normalized.endsWith('/task.json')) {
      taskFiles.add(normalized);
    }
  }

  let inspectedFiles = 0;
  for (const file of [...taskFiles].sort()) {
    const loaded = loadTrellisTaskMetadataFile(file, { deletedIsMissing: true });
    if (loaded.status === 'missing') {
      continue;
    }

    inspectedFiles += 1;
    if (loaded.status !== 'loaded') {
      fail(`${file} ${loaded.message}`);
      continue;
    }

    const artifact = parseTrellisTaskArtifactPath(file);
    if (!artifact) {
      fail(
        `${file} is not in a supported Trellis task layout; use ` +
          '.trellis/tasks/MM-DD-name/task.json or .trellis/tasks/archive/YYYY-MM/name/task.json.',
      );
      continue;
    }

    let record;
    try {
      record = JSON.parse(loaded.text);
    } catch (error) {
      fail(
        `${file} could not be parsed as JSON while checking task metadata integrity: ` +
          thrownValueMessage(error),
      );
      continue;
    }

    if (!isPlainObject(record)) {
      fail(`${file} must contain a JSON object while checking task metadata integrity.`);
      continue;
    }

    for (const issue of validateTrellisBookkeepingMetadata(record, artifact.taskDir, artifact.archived)) {
      fail(`${file} field ${issue}.`);
    }
    validateTrellisTaskMetadataLinks(file, artifact.taskDir, record);
  }

  if (inspectedFiles === 0) {
    if (failures.length === failureStart) {
      pass('no changed Trellis task metadata records require integrity checks.');
    }
    return;
  }

  if (failures.length === failureStart) {
    pass(`checked ${inspectedFiles} changed Trellis task metadata record(s) for identity, lifecycle, branch, and link integrity.`);
  }
}

function checkChangedTrellisTaskTopologySemantics() {
  const failureStart = failures.length;
  const diff = currentChangedPaths();
  if (diff === null) {
    warn('could not inspect current diff for changed Trellis task topology semantics.');
    return;
  }

  const changedTaskFiles = new Set();
  const changedTaskDirectories = new Set();
  const changedArchivedTaskRecords = new Set();
  for (const path of diff.paths) {
    const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
    const archivedTaskRecord =
      /^\.trellis\/tasks\/archive\/\d{4}-\d{2}\/(\d{2}-\d{2}-[^/]+)\/task\.json$/.exec(normalized);
    if (archivedTaskRecord) {
      changedArchivedTaskRecords.add(archivedTaskRecord[1]);
    }

    const artifact = parseActiveTrellisTaskTopologyPath(path);
    if (!artifact) {
      continue;
    }
    changedTaskDirectories.add(artifact.taskDir);
    if (artifact.artifact === 'task.json') {
      changedTaskFiles.add(`${artifact.taskDir}/task.json`);
    }
  }

  let inspectedPlanningBases = 0;
  for (const file of [...changedTaskFiles].sort()) {
    const loaded = loadTrellisTaskMetadataFile(file, { deletedIsMissing: true });
    if (loaded.status !== 'loaded') {
      // The structural metadata check owns deleted move sources and unsafe or
      // unreadable changed task records.
      continue;
    }

    let record;
    try {
      record = JSON.parse(loaded.text);
    } catch {
      continue;
    }
    if (
      !isPlainObject(record) ||
      record.status !== 'planning' ||
      record.branch !== null ||
      !isTrellisTaskDirectoryName(record.parent)
    ) {
      continue;
    }

    inspectedPlanningBases += 1;
    const parent = loadReferencedTrellisTaskRecord(file, 'parent', record.parent, {
      reportFailures: false,
    });
    if (!parent) {
      // The structural metadata check already emitted the authoritative linked
      // record diagnostic.
      continue;
    }
    for (const issue of validateTrellisPlanningBaseInheritance(record, parent)) {
      fail(`${file} field ${issue}.`);
    }
  }

  let inspectedRootBases = 0;
  const rootDefaultBranch = changedTaskFiles.size > 0 ? trellisRootDefaultBranchName() : '';
  if (rootDefaultBranch) {
    for (const file of [...changedTaskFiles].sort()) {
      const loaded = loadTrellisTaskMetadataFile(file, { deletedIsMissing: true });
      if (loaded.status !== 'loaded') {
        // The structural metadata check owns deleted move sources and unsafe or
        // unreadable changed task records.
        continue;
      }

      let record;
      try {
        record = JSON.parse(loaded.text);
      } catch {
        continue;
      }
      if (
        !isPlainObject(record) ||
        (record.parent !== null && record.parent !== undefined) ||
        typeof record.base_branch !== 'string' ||
        record.base_branch.trim().length === 0
      ) {
        continue;
      }

      inspectedRootBases += 1;
      for (const issue of validateTrellisRootTaskBaseBranch(record, rootDefaultBranch)) {
        fail(`${file} field ${issue}.`);
      }
    }
  }

  let inspectedParentPrds = 0;
  for (const taskDir of [...changedTaskDirectories].sort()) {
    const taskFile = `${taskDir}/task.json`;
    const taskLoaded = loadTrellisTaskMetadataFile(taskFile, { deletedIsMissing: true });
    if (taskLoaded.status !== 'loaded') {
      const taskName = taskDir.slice('.trellis/tasks/'.length);
      const movedToChangedArchive =
        !pathEntryExists(taskDir) && changedArchivedTaskRecords.has(taskName);
      if (!changedTaskFiles.has(taskFile) && !movedToChangedArchive) {
        fail(`${taskFile} ${taskLoaded.message} while checking active parent PRD child representation.`);
      }
      continue;
    }

    let record;
    try {
      record = JSON.parse(taskLoaded.text);
    } catch (error) {
      if (!changedTaskFiles.has(taskFile)) {
        fail(
          `${taskFile} could not be parsed as JSON while checking active parent PRD child representation: ` +
            thrownValueMessage(error),
        );
      }
      continue;
    }
    if (!isPlainObject(record)) {
      if (!changedTaskFiles.has(taskFile)) {
        fail(`${taskFile} must contain a JSON object while checking active parent PRD child representation.`);
      }
      continue;
    }
    if (!ACTIVE_TRELLIS_TASK_STATUSES.has(record.status)) {
      continue;
    }
    if (record.children === undefined || (Array.isArray(record.children) && record.children.length === 0)) {
      continue;
    }
    if (!Array.isArray(record.children)) {
      if (!changedTaskFiles.has(taskFile)) {
        fail(`${taskFile} field children must be an array while checking active parent PRD child representation.`);
      }
      continue;
    }
    if (
      record.children.length > MAX_TRELLIS_TASK_LINKS ||
      record.children.some((child) => !isTrellisTaskDirectoryName(child))
    ) {
      if (!changedTaskFiles.has(taskFile)) {
        fail(`${taskFile} field children cannot be verified while checking active parent PRD child representation.`);
      }
      continue;
    }

    inspectedParentPrds += 1;
    const prdFile = `${taskDir}/prd.md`;
    const prdLoaded = loadTrellisTaskPrdFile(prdFile, { deletedIsMissing: true });
    if (prdLoaded.status !== 'loaded') {
      fail(`${prdFile} ${prdLoaded.message}; the active task declares child metadata that must be represented in its PRD.`);
      continue;
    }

    const missingChildren = findMissingTrellisChildReferences(prdLoaded.text, record.children);
    if (missingChildren.length === 1) {
      fail(
        `${prdFile} does not reference declared child ${missingChildren[0]}; ` +
          'add the exact task ID or remove stale children metadata.',
      );
    } else if (missingChildren.length > 1) {
      fail(
        `${prdFile} does not reference declared children ${missingChildren.join(', ')}; ` +
          'add every exact task ID or remove stale children metadata.',
      );
    }
  }

  if (failures.length !== failureStart) {
    return;
  }
  if (inspectedPlanningBases === 0 && inspectedRootBases === 0 && inspectedParentPrds === 0) {
    pass('no changed Trellis task topology requires semantic validation.');
    return;
  }
  pass(
    `checked ${inspectedPlanningBases} deferred planning child base(s), ` +
      `${inspectedRootBases} root task base branch(es), and ` +
      `${inspectedParentPrds} active parent PRD child map(s) for topology semantics.`,
  );
}

function parseActiveTrellisTaskTopologyPath(path) {
  const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
  const match = /^\.trellis\/tasks\/(\d{2}-\d{2}-[^/]+)\/(task\.json|prd\.md)$/.exec(normalized);
  if (!match) {
    return null;
  }
  return {
    taskDir: `.trellis/tasks/${match[1]}`,
    artifact: match[2],
  };
}

export function validateTrellisPlanningBaseInheritance(record, parentRecord) {
  if (
    !isPlainObject(record) ||
    !isPlainObject(parentRecord) ||
    record.status !== 'planning' ||
    record.branch !== null ||
    !isTrellisTaskDirectoryName(record.parent) ||
    typeof record.base_branch !== 'string' ||
    record.base_branch.trim().length === 0
  ) {
    return [];
  }

  const allowedTargets = [];
  if (typeof parentRecord.base_branch === 'string' && parentRecord.base_branch.trim().length > 0) {
    allowedTargets.push(parentRecord.base_branch.trim());
  }
  if (
    ACTIVE_TRELLIS_TASK_STATUSES.has(parentRecord.status) &&
    typeof parentRecord.branch === 'string' &&
    parentRecord.branch.trim().length > 0
  ) {
    allowedTargets.push(parentRecord.branch.trim());
  }
  const uniqueAllowedTargets = [...new Set(allowedTargets)];
  if (uniqueAllowedTargets.length === 0) {
    return ['base_branch cannot be verified because the parent has no non-empty base_branch or active branch'];
  }
  if (uniqueAllowedTargets.includes(record.base_branch.trim())) {
    return [];
  }
  return [
    `base_branch ${JSON.stringify(record.base_branch.trim())} must equal parent base_branch or active branch (` +
      `${uniqueAllowedTargets.map((target) => JSON.stringify(target)).join(', ')})`,
  ];
}

export function validateTrellisRootTaskBaseBranch(record, defaultBranchName) {
  if (
    !isPlainObject(record) ||
    (record.parent !== null && record.parent !== undefined) ||
    typeof record.base_branch !== 'string' ||
    record.base_branch.trim().length === 0 ||
    typeof defaultBranchName !== 'string' ||
    defaultBranchName.trim().length === 0
  ) {
    return [];
  }
  const exemption = isPlainObject(record.meta) ? record.meta.base_branch_exemption : undefined;
  if (typeof exemption === 'string' && exemption.trim().length > 0) {
    return [];
  }
  const target = record.base_branch.trim();
  if (target === defaultBranchName.trim()) {
    return [];
  }
  return [
    `root task base_branch ${JSON.stringify(target)} must equal the repository default branch ` +
      `${JSON.stringify(defaultBranchName.trim())} or carry a meta.base_branch_exemption reason ` +
      '(python3 ./.trellis/scripts/task.py set-meta <task-dir> base_branch_exemption "<reason>")',
  ];
}

export function findMissingTrellisChildReferences(text, children) {
  if (typeof text !== 'string' || !Array.isArray(children)) {
    return [];
  }
  const uniqueChildren = [...new Set(children.filter(isTrellisTaskDirectoryName))].sort();
  return uniqueChildren.filter((child) => {
    const pattern = new RegExp(
      `(^|[^A-Za-z0-9._-])${escapeRegExp(child)}(?=$|[^A-Za-z0-9._-])`,
      'm',
    );
    return !pattern.test(text);
  });
}

export function validateTrellisTaskMetadata(record, taskDir, archived) {
  const issues = [];
  const idValid = typeof record.id === 'string' && record.id.trim().length > 0;
  const nameValid = typeof record.name === 'string' && record.name.trim().length > 0;

  if (!idValid) {
    issues.push('id must be a non-empty string');
  }
  if (!nameValid) {
    issues.push('name must be a non-empty string');
  }
  if (idValid && nameValid && record.id !== record.name) {
    issues.push('id must equal name');
  }

  const taskDirectoryName = taskDir.slice(taskDir.lastIndexOf('/') + 1);
  const directoryMatch = /^\d{2}-\d{2}-(.+)$/.exec(taskDirectoryName);
  if (!directoryMatch && !archived) {
    issues.push('name cannot be verified because the task directory must use the MM-DD-name form');
  } else if (directoryMatch && nameValid && record.name !== directoryMatch[1]) {
    issues.push(`name must match the dated task directory suffix "${directoryMatch[1]}"`);
  }

  if (!TRELLIS_TASK_STATUSES.has(record.status)) {
    issues.push('status must be one of planning, in_progress, review, completed');
  } else if (ACTIVE_TRELLIS_TASK_STATUSES.has(record.status)) {
    if (record.completedAt !== null) {
      issues.push(`completedAt must be null when status is ${record.status}`);
    }
  } else if (record.status === 'completed') {
    if (typeof record.completedAt !== 'string' || record.completedAt.trim().length === 0) {
      issues.push('completedAt must be a non-empty completion timestamp when status is completed');
    }
    if (!archived) {
      issues.push('status completed requires the task record to be under .trellis/tasks/archive/');
    }
  }

  if (typeof record.base_branch !== 'string' || record.base_branch.trim().length === 0) {
    issues.push('base_branch must be a non-empty string');
  }
  if (record.branch !== null && record.branch !== undefined) {
    if (typeof record.branch !== 'string' || record.branch.trim().length === 0) {
      issues.push('branch must be null or a non-empty string');
    } else if (
      typeof record.base_branch === 'string' &&
      record.branch.trim() === record.base_branch.trim()
    ) {
      issues.push('branch must differ from base_branch');
    }
  }

  if (
    record.parent !== null &&
    record.parent !== undefined &&
    !isTrellisTaskDirectoryName(record.parent)
  ) {
    issues.push('parent must be null or a safe MM-DD-name task directory reference');
  }
  if (record.children !== undefined) {
    if (!Array.isArray(record.children)) {
      issues.push('children must be an array of task directory references');
    } else {
      if (record.children.length > MAX_TRELLIS_TASK_LINKS) {
        issues.push(`children must contain at most ${MAX_TRELLIS_TASK_LINKS} task directory references`);
      }
      const invalidChild = record.children.find((child) => !isTrellisTaskDirectoryName(child));
      if (invalidChild !== undefined) {
        issues.push('children must contain only safe MM-DD-name task directory references');
      }
    }
  }

  issues.push(...validateTrellisTaskPriorityProvenance(record));

  return issues;
}

export function validateTrellisBookkeepingMetadata(record, taskDir, archived) {
  if (!isPlainObject(record)) {
    return ['record must be a JSON object'];
  }

  const issues = validateTrellisTaskMetadata(record, taskDir, archived);
  for (const field of ['title', 'description']) {
    if (typeof record[field] !== 'string' || record[field].trim().length === 0) {
      // Name the repair, not just the constraint: at seeding time this is the
      // most common finding and the operator is holding the command that
      // produced it. Deliberately not `set-meta` -- that subcommand is absent
      // from the older vendored task.py, i.e. exactly the consumers that hit
      // this. `task.py create` carries both of these in every revision, with
      // the title positional and the description behind a flag.
      const repair = field === 'description'
        ? 're-create the task passing a real --description'
        : 're-create the task with a real title argument';
      issues.push(`${field} must be a non-empty string; ${repair}`);
    }
  }
  if (!isTrellisTimestamp(record.createdAt)) {
    issues.push('createdAt must be a valid date or timestamp');
  }
  if (
    record.status === 'completed' &&
    typeof record.completedAt === 'string' &&
    !isTrellisTimestamp(record.completedAt)
  ) {
    issues.push('completedAt must be a valid completion date or timestamp');
  } else if (
    record.status === 'completed' &&
    isTrellisTimestamp(record.createdAt) &&
    isTrellisTimestamp(record.completedAt) &&
    Date.parse(record.completedAt) < Date.parse(record.createdAt)
  ) {
    issues.push('completedAt must not be earlier than createdAt');
  }
  return issues;
}

function isTrellisTimestamp(value) {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) {
    return false;
  }
  if (!/^\d{4}-\d{2}-\d{2}(?:T[^\s]+)?$/.test(value)) {
    return false;
  }
  const datePart = value.slice(0, 10);
  const parsedDate = new Date(`${datePart}T00:00:00Z`);
  if (!Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== datePart) {
    return false;
  }
  return value.length === 10 || Number.isFinite(Date.parse(value));
}

function validateTrellisTaskPriorityProvenance(record) {
  if (
    !isPlainObject(record) ||
    !isPlainObject(record.meta) ||
    !Object.prototype.hasOwnProperty.call(record.meta, 'priorityProvenance')
  ) {
    return [];
  }

  const provenance = record.meta.priorityProvenance;
  if (!isPlainObject(provenance)) {
    return ['meta.priorityProvenance must be an object'];
  }

  const issues = [];
  const priorityValid = TRELLIS_TASK_PRIORITIES.has(record.priority);
  const sourcePriorityValid = TRELLIS_TASK_PRIORITIES.has(provenance.sourcePriority);
  if (!priorityValid) {
    issues.push(
      'priority must be one of P0, P1, P2, P3 when meta.priorityProvenance is declared',
    );
  }
  if (!sourcePriorityValid) {
    issues.push('meta.priorityProvenance.sourcePriority must be one of P0, P1, P2, P3');
  }
  if (
    priorityValid &&
    sourcePriorityValid &&
    record.priority === provenance.sourcePriority
  ) {
    issues.push(
      'meta.priorityProvenance.sourcePriority must differ from priority; ' +
        'remove provenance when priority is unchanged',
    );
  }

  if (
    typeof provenance.rationale !== 'string' ||
    provenance.rationale.trim().length === 0
  ) {
    issues.push('meta.priorityProvenance.rationale must be a non-empty string');
  } else if (
    provenance.rationale.trim().length > MAX_TRELLIS_PRIORITY_RATIONALE_LENGTH
  ) {
    issues.push(
      `meta.priorityProvenance.rationale must be at most ${MAX_TRELLIS_PRIORITY_RATIONALE_LENGTH} characters`,
    );
  }

  return issues;
}

function validateTrellisTaskMetadataLinks(file, taskDir, record) {
  const taskDirectoryName = taskDir.slice(taskDir.lastIndexOf('/') + 1);

  if (isTrellisTaskDirectoryName(record.parent)) {
    const parent = loadReferencedTrellisTaskRecord(file, 'parent', record.parent);
    if (parent && (!Array.isArray(parent.children) || !parent.children.includes(taskDirectoryName))) {
      fail(
        `${file} field parent references ${record.parent}, but its children field does not include ${taskDirectoryName}.`,
      );
    }
  }

  if (!Array.isArray(record.children)) {
    return;
  }
  const childNames = record.children
    .filter(isTrellisTaskDirectoryName)
    .slice(0, MAX_TRELLIS_TASK_LINKS);
  for (const childName of new Set(childNames)) {
    const child = loadReferencedTrellisTaskRecord(file, 'children', childName);
    if (child && child.parent !== taskDirectoryName) {
      fail(
        `${file} field children references ${childName}, but its parent field is not ${taskDirectoryName}.`,
      );
    }
  }
}

function loadReferencedTrellisTaskRecord(sourceFile, field, taskDirectoryName, options = {}) {
  const reportFailures = options.reportFailures !== false;
  const reportFailure = (message) => {
    if (reportFailures) {
      fail(message);
    }
  };
  const located = locateTrellisTaskRecord(taskDirectoryName);
  if (located.error) {
    reportFailure(`${sourceFile} field ${field} references ${taskDirectoryName}, but the record cannot be verified: ${located.error}.`);
    return null;
  }
  if (located.paths.length === 0) {
    reportFailure(`${sourceFile} field ${field} references missing task ${taskDirectoryName}.`);
    return null;
  }
  if (located.paths.length > 1) {
    reportFailure(
      `${sourceFile} field ${field} references ambiguous task ${taskDirectoryName}: ${located.paths.join(', ')}.`,
    );
    return null;
  }

  const referencedFile = located.paths[0];
  const loaded = loadTrellisTaskMetadataFile(referencedFile);
  if (loaded.status !== 'loaded') {
    reportFailure(
      `${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} ${loaded.message}.`,
    );
    return null;
  }

  let record;
  try {
    record = JSON.parse(loaded.text);
  } catch (error) {
    reportFailure(
      `${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} could not be parsed as JSON: ` +
        thrownValueMessage(error),
    );
    return null;
  }
  if (!isPlainObject(record)) {
    reportFailure(`${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} does not contain a JSON object.`);
    return null;
  }
  return record;
}

function locateTrellisTaskRecord(taskDirectoryName) {
  const paths = [];
  const activeCandidate = `.trellis/tasks/${taskDirectoryName}`;
  const activeResult = trellisTaskRecordCandidate(activeCandidate);
  if (activeResult.error) {
    return { paths, error: activeResult.error };
  }
  if (activeResult.path) {
    paths.push(activeResult.path);
  }

  const archiveRoot = resolve(rootDir, '.trellis/tasks/archive');
  let monthEntries;
  try {
    monthEntries = readdirSync(archiveRoot, { withFileTypes: true });
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return { paths, error: '' };
    }
    return { paths, error: `.trellis/tasks/archive could not be inspected: ${thrownValueMessage(error)}` };
  }

  for (const monthEntry of monthEntries) {
    if (
      monthEntry.isSymbolicLink() ||
      !monthEntry.isDirectory() ||
      !/^\d{4}-\d{2}$/.test(monthEntry.name)
    ) {
      continue;
    }
    const archivedCandidate = `.trellis/tasks/archive/${monthEntry.name}/${taskDirectoryName}`;
    const archivedResult = trellisTaskRecordCandidate(archivedCandidate);
    if (archivedResult.error) {
      return { paths, error: archivedResult.error };
    }
    if (archivedResult.path) {
      paths.push(archivedResult.path);
    }
  }

  return { paths, error: '' };
}

function trellisTaskRecordCandidate(taskDir) {
  const absoluteTaskDir = resolve(rootDir, taskDir);
  let directoryEntry;
  try {
    directoryEntry = lstatSync(absoluteTaskDir);
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return { path: '', error: '' };
    }
    return { path: '', error: `${taskDir} could not be inspected: ${thrownValueMessage(error)}` };
  }
  if (directoryEntry.isSymbolicLink()) {
    return { path: '', error: `${taskDir} is a symlink` };
  }
  if (!directoryEntry.isDirectory()) {
    return { path: '', error: `${taskDir} is not a directory` };
  }

  const taskFile = `${taskDir}/task.json`;
  try {
    lstatSync(resolve(rootDir, taskFile));
    return { path: taskFile, error: '' };
  } catch (error) {
    if (error?.code === 'ENOENT') {
      return { path: '', error: '' };
    }
    return { path: '', error: `${taskFile} could not be inspected: ${thrownValueMessage(error)}` };
  }
}

function loadTrellisTaskMetadataFile(file, options = {}) {
  return loadBoundedTrellisTaskArtifact(file, 'task metadata', options);
}

function loadTrellisTaskPrdFile(file, options = {}) {
  return loadBoundedTrellisTaskArtifact(file, 'task PRD', options);
}

function loadBoundedTrellisTaskArtifact(file, artifactLabel, options = {}) {
  const absoluteFile = resolve(rootDir, file);
  let pathEntry;
  try {
    pathEntry = lstatSync(absoluteFile);
  } catch (error) {
    if (options.deletedIsMissing && error?.code === 'ENOENT') {
      return { status: 'missing', message: 'is missing' };
    }
    return { status: 'unreadable', message: `could not be inspected: ${thrownValueMessage(error)}` };
  }
  if (pathEntry.isSymbolicLink()) {
    return { status: 'unsafe', message: `is a symlink; ${artifactLabel} must be a regular file` };
  }
  if (!pathEntry.isFile()) {
    return { status: 'unsafe', message: `is not a regular file; ${artifactLabel} must be a regular file` };
  }
  if (pathEntry.size > config.untrackedFileReadLimitBytes) {
    return {
      status: 'oversized',
      message: `exceeds the bounded ${artifactLabel} read limit of ${config.untrackedFileReadLimitBytes} bytes`,
    };
  }

  const content = boundedUntrackedFileText(file);
  if (!content || content.status === 'unreadable') {
    return { status: 'unreadable', message: 'could not be read safely as a regular file' };
  }
  if (content.status === 'oversized') {
    return {
      status: 'oversized',
      message: `exceeds the bounded ${artifactLabel} read limit of ${config.untrackedFileReadLimitBytes} bytes`,
    };
  }
  return { status: 'loaded', text: content.text, message: '' };
}

function isTrellisTaskDirectoryName(value) {
  return (
    typeof value === 'string' &&
    value.length <= MAX_TRELLIS_TASK_REFERENCE_LENGTH &&
    /^\d{2}-\d{2}-[a-z0-9][a-z0-9._-]*$/i.test(value)
  );
}

function checkTrellisTaskContextManifests() {
  const failureStart = failures.length;
  const diff = currentChangedPaths();
  if (diff === null) {
    warn('could not inspect current diff for Trellis task context manifests.');
    return;
  }

  const contextFiles = new Set();
  for (const path of diff.paths) {
    const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
    const contextArtifact =
      normalized.startsWith('.trellis/tasks/') &&
      (normalized.endsWith('/implement.jsonl') || normalized.endsWith('/check.jsonl'));
    const taskMetadata =
      normalized.startsWith('.trellis/tasks/') && normalized.endsWith('/task.json');
    if (!contextArtifact && !taskMetadata) {
      continue;
    }

    const artifact = parseTrellisTaskArtifactPath(normalized);
    if (!artifact) {
      if (contextArtifact && pathEntryExists(normalized)) {
        fail(
          `${normalized} is not in a supported Trellis task layout; use ` +
            '.trellis/tasks/MM-DD-name/{implement,check}.jsonl or ' +
            '.trellis/tasks/archive/YYYY-MM/name/{implement,check}.jsonl.',
        );
      }
      continue;
    }

    if (contextArtifact) {
      contextFiles.add(`${artifact.taskDir}/${artifact.artifact}`);
      continue;
    }

    const loaded = loadTrellisTaskMetadataFile(normalized, { deletedIsMissing: true });
    if (loaded.status !== 'loaded') {
      continue;
    }
    let record;
    try {
      record = JSON.parse(loaded.text);
    } catch {
      continue;
    }
    if (
      isPlainObject(record) &&
      TRELLIS_TASK_STATUSES.has(record.status) &&
      record.status !== 'planning'
    ) {
      contextFiles.add(`${artifact.taskDir}/implement.jsonl`);
      contextFiles.add(`${artifact.taskDir}/check.jsonl`);
    }
  }

  let inspectedFiles = 0;
  let exemptScaffolds = 0;
  for (const file of contextFiles) {
    if (!isRegularFile(file)) {
      continue;
    }

    inspectedFiles += 1;
    const text = readText(file);
    const loneScaffold = isPristineTrellisTaskContextScaffold(text);
    if (loneScaffold) {
      exemptScaffolds += 1;
    }
    for (const issue of findTrellisTaskContextIssues(file, text)) {
      if (issue.kind === 'seed') {
        if (loneScaffold) {
          continue;
        }
        fail(
          `${issue.file}:${issue.line} still contains a generated _example scaffold row; ` +
            'replace it with grounded {"file": "<path>", "reason": "<why>"} context or leave the file empty.',
        );
        continue;
      }
      if (issue.kind === 'malformed') {
        fail(
          `${issue.file}:${issue.line} is not valid JSONL; ` +
            'replace the malformed non-empty row with one JSON object or remove it.',
        );
        continue;
      }
      if (issue.kind === 'self_reference') {
        fail(`${issue.file}:${issue.line} ${TRELLIS_TASK_CONTEXT_SELF_REFERENCE_REPAIR}`);
        continue;
      }
      fail(
        `${issue.file}:${issue.line} contains a task context reference outside the allowed spec/research roots; ` +
          'use .trellis/spec/** or .trellis/tasks/**/research/** only, never code or test paths.',
      );
    }
  }

  if (inspectedFiles === 0) {
    if (failures.length === failureStart) {
      pass('no changed Trellis task context manifests require validation.');
    }
    return;
  }

  if (failures.length === failureStart) {
    const exemptSuffix = exemptScaffolds
      ? ` ${exemptScaffolds} untouched lone _example scaffold(s) are exempt (advisory/unfilled).`
      : '';
    pass(
      `checked ${inspectedFiles} changed Trellis task context file(s) for valid JSONL, generated _example scaffold rows, and spec/research-only references.${exemptSuffix}`,
    );
  }
}

function checkTrellisPlanningPlaceholders() {
  const failureStart = failures.length;
  const diff = currentChangedPaths();

  if (diff === null) {
    warn('could not inspect the current diff for Trellis planning placeholders.');
    return;
  }

  let inspectedFiles = 0;
  for (const path of diff.paths) {
    const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
    if (!normalized.startsWith('.trellis/tasks/') || !normalized.endsWith('/prd.md')) {
      continue;
    }
    if (!isRegularFile(normalized)) {
      continue;
    }

    inspectedFiles += 1;
    for (const placeholder of findTrellisPlanningPlaceholders(normalized, readText(normalized))) {
      fail(
        `${placeholder.file}:${placeholder.line} still contains the generated placeholder ` +
          `${JSON.stringify(placeholder.text)}; replace it with a real requirement, ` +
          'acceptance criterion, or goal before the task advances.',
      );
    }
  }

  if (inspectedFiles === 0) {
    if (failures.length === failureStart) {
      pass('no changed Trellis PRD requires placeholder validation.');
    }
    return;
  }

  if (failures.length === failureStart) {
    pass(`checked ${inspectedFiles} changed Trellis PRD(s) for generated TBD placeholders.`);
  }
}

function checkCompletedTrellisTaskLocation() {
  const failureStart = failures.length;
  const taskRoot = resolve(rootDir, '.trellis', 'tasks');
  if (!existsSync(taskRoot)) {
    pass('no Trellis task root is present; completed-task location check skipped.');
    return;
  }

  let entries;
  try {
    entries = readdirSync(taskRoot, { withFileTypes: true })
      .filter(
        (entry) =>
          entry.name !== 'archive' &&
          !entry.isSymbolicLink() &&
          entry.isDirectory(),
      )
      .sort((left, right) => left.name.localeCompare(right.name));
  } catch (error) {
    fail(`.trellis/tasks could not be inspected for completed active-root tasks: ${thrownValueMessage(error)}`);
    return;
  }

  let inspected = 0;
  let completed = 0;
  for (const entry of entries) {
    const taskFile = `.trellis/tasks/${entry.name}/task.json`;
    if (!isRegularFile(taskFile)) {
      continue;
    }

    inspected += 1;
    let task;
    try {
      task = readJson(taskFile);
    } catch (error) {
      fail(`${taskFile} could not be parsed as JSON while checking completed-task location: ${thrownValueMessage(error)}`);
      continue;
    }

    if (task?.status !== 'completed') {
      continue;
    }

    completed += 1;
    fail(
      `${taskFile} has status completed outside .trellis/tasks/archive/; ` +
        `archive it with python3 ./.trellis/scripts/task.py archive ${entry.name}.`,
    );
  }

  if (completed === 0 && failures.length === failureStart) {
    pass(`checked ${inspected} active-root Trellis task record(s); none is completed outside archive.`);
  }
}

export function parseTrellisTaskArtifactPath(path) {
  const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
  const match = /^\.trellis\/tasks\/((?:archive\/\d{4}-\d{2}\/[^/]+)|\d{2}-\d{2}-[^/]+)\/(task\.json|implement\.jsonl|check\.jsonl)$/.exec(normalized);
  if (!match || match[1] === 'archive') {
    return null;
  }

  return {
    taskDir: `.trellis/tasks/${match[1]}`,
    artifact: match[2],
    archived: match[1].startsWith('archive/'),
  };
}

export function isPristineTrellisTaskContextScaffold(text) {
  const rows = text.split(/\r?\n/).filter((line) => line.trim());
  if (rows.length !== 1) {
    return false;
  }

  let record;
  try {
    record = JSON.parse(rows[0]);
  } catch {
    return false;
  }

  return (
    isPlainObject(record) &&
    Object.keys(record).length === 1 &&
    Object.prototype.hasOwnProperty.call(record, '_example')
  );
}

// "This manifest carries no usable row" is a property of the file, not of any
// row, so findTrellisTaskContextIssues cannot express it: that function reaches
// a row only when the line is non-blank, and reports what is wrong with rows it
// finds. A file with none is invisible to it. Both walk the same split with the
// same blank-line skip so they cannot disagree about what counts as a row.
//
// A row counts here when it carries a `file` key, even when that reference is
// rejected: a manifest citing a forbidden path is filled-but-wrong, and the
// reference or self_reference finding already names that defect precisely.
export function countTrellisTaskContextRows(text) {
  let usable = 0;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) {
      continue;
    }
    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }
    if (isPlainObject(record) && Object.prototype.hasOwnProperty.call(record, 'file')) {
      usable += 1;
    }
  }
  return usable;
}

export function findTrellisTaskContextSeedRows(file, text) {
  return findTrellisTaskContextIssues(file, text)
    .filter((issue) => issue.kind === 'seed')
    .map(({ file: issueFile, line }) => ({ file: issueFile, line }));
}

export function isTrellisTaskContextReference(value) {
  if (typeof value !== 'string' || value.length === 0 || value !== value.trim()) {
    return false;
  }

  const normalized = normalizePathSeparators(value).replace(/^\.\//, '');
  const pathWithoutTrailingSlash = normalized.endsWith('/') ? normalized.slice(0, -1) : normalized;
  const segments = pathWithoutTrailingSlash.split('/');
  if (
    URI_SCHEME_PATTERN.test(value) ||
    normalized.startsWith('/') ||
    segments.some((segment) => segment === '' || segment === '.' || segment === '..')
  ) {
    return false;
  }

  return (
    pathWithoutTrailingSlash === '.trellis/spec' ||
    pathWithoutTrailingSlash.startsWith('.trellis/spec/') ||
    /^\.trellis\/tasks\/(?:archive\/\d{4}-\d{2}\/)?[^/]+\/research(?:\/.+)?$/.test(pathWithoutTrailingSlash)
  );
}

// A task's own `research/**` is inside the allowed roots, so the root test above
// accepts it for the whole life of the task -- and `task.py archive` then moves
// the directory out from under the pointer, in the same completion bundle that
// publishes it. Comparing the cited task directory against the citing file's own
// is the only part of that failure that is decidable here: a SIBLING task's
// research is equally doomed when that sibling archives later, but nothing in
// the current tree distinguishes it from a citation that will stay valid.
export function trellisTaskContextOwnerDirectory(value) {
  if (typeof value !== 'string' || value.length === 0) {
    return '';
  }

  // Normalize exactly as isTrellisTaskContextReference does, or a `./`-prefixed
  // or backslash-separated self-citation walks past the comparison.
  const normalized = normalizePathSeparators(value).replace(/^\.\//, '');
  const match = /^(\.trellis\/tasks\/(?:archive\/\d{4}-\d{2}\/)?[^/]+)\//.exec(normalized);
  return match ? match[1] : '';
}

// `task.py create` seeds prd.md from `_default_prd_content`, which writes three
// placeholders: the Goal body `TBD.` when no description was supplied, a `- TBD`
// requirement bullet, and a `- [ ] TBD` acceptance criterion
// (.trellis/scripts/common/task_store.py:196-213). Nothing anywhere rejects
// them: the ready gate in .trellis/workflow.md is scoped to the two manifests,
// and the merge-time preflight had no rule of its own.
//
// Match the whole line, not a bare substring -- a PRD is allowed to DISCUSS the
// string TBD in prose, and the PRD for the task that added this rule does.
export function findTrellisPlanningPlaceholders(file, text) {
  if (typeof text !== 'string') {
    return [];
  }

  const placeholders = [];
  for (const [index, line] of text.split(/\r?\n/).entries()) {
    const trimmed = line.trim();
    if (
      trimmed === 'TBD.'
      || trimmed === 'TBD'
      || /^[-*]\s+TBD\.?$/.test(trimmed)
      || /^[-*]\s+\[[ xX]\]\s+TBD\.?$/.test(trimmed)
    ) {
      placeholders.push({ file, line: index + 1, text: trimmed });
    }
  }

  return placeholders;
}

export function findTrellisTaskContextIssues(file, text) {
  const issues = [];
  const owner = trellisTaskContextOwnerDirectory(file);

  for (const [index, line] of text.split(/\r?\n/).entries()) {
    if (!line.trim()) {
      continue;
    }

    let record;
    try {
      record = JSON.parse(line);
    } catch {
      issues.push({ file, line: index + 1, kind: 'malformed' });
      continue;
    }

    if (isPlainObject(record) && Object.prototype.hasOwnProperty.call(record, '_example')) {
      issues.push({ file, line: index + 1, kind: 'seed' });
      continue;
    }
    if (isPlainObject(record) && Object.prototype.hasOwnProperty.call(record, 'file')) {
      if (!isTrellisTaskContextReference(record.file)) {
        issues.push({ file, line: index + 1, kind: 'reference' });
      } else if (owner && trellisTaskContextOwnerDirectory(record.file) === owner) {
        issues.push({ file, line: index + 1, kind: 'self_reference' });
      }
    }
  }

  return issues;
}

function checkTrellisJournalRecords() {
  const failureStart = failures.length;
  const workspaceRoot = resolve(rootDir, '.trellis/workspace');
  const baselineRef = journalBaselineRef();
  const baselineJournalFiles = baselineRef
    ? gitFilesAtRef(baselineRef, '.trellis/workspace').filter((file) =>
        /^\.trellis\/workspace\/[^/]+\/journal-\d+\.md$/.test(file),
      )
    : [];
  const workspacePresent = exists('.trellis/workspace');

  if (!workspacePresent && baselineJournalFiles.length === 0) {
    pass('.trellis/workspace is not present in the working tree or review base; Trellis journal checks skipped.');
    return;
  }

  const currentDeveloperDirs = workspacePresent
    ? readdirSync(workspaceRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => resolve(workspaceRoot, entry.name))
    : [];
  const developerRelatives = [...new Set([
    ...currentDeveloperDirs.map(absoluteToRelative),
    ...baselineJournalFiles.map((file) => dirname(file)),
  ])].sort();
  let completedSessions = 0;
  let comparedSessions = 0;
  let baselineSessionsCompared = 0;

  for (const developerRelative of developerRelatives) {
    const developerDir = resolve(rootDir, developerRelative);
    const indexFile = `${developerRelative}/index.md`;
    const journalFiles = exists(developerRelative)
      ? readdirSync(developerDir, { withFileTypes: true })
          .filter((entry) => entry.isFile() && /^journal-\d+\.md$/.test(entry.name))
          .map((entry) => `${developerRelative}/${entry.name}`)
          .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
      : [];
    const journalSessions = [];
    const baselineJournalSessions = [];

    for (const journalFile of journalFiles) {
      journalSessions.push(...parseJournalSessions(journalFile));
    }

    for (const journalFile of baselineJournalFiles.filter((file) => dirname(file) === developerRelative)) {
      baselineJournalSessions.push(
        ...parseJournalSessionsFromText(journalFile, gitFileAtRef(baselineRef, journalFile)),
      );
    }

    baselineSessionsCompared += baselineJournalSessions.length;
    for (const issue of findHistoricalTrellisJournalSessionEdits(
      baselineJournalSessions,
      journalSessions,
    )) {
      const action = issue.kind === 'removed' ? 'removes' : 'modifies';
      fail(
        `${issue.session.file}:${issue.session.startLine} ${action} historical Session ${issue.session.number} from ${baselineRef}; ` +
          'Trellis journal history is append-only. Restore that session and edit the intended current session by heading.',
      );
    }

    if (journalSessions.length === 0) {
      continue;
    }

    let indexSessions = null;
    if (!exists(indexFile)) {
      fail(`${indexFile} is missing; cannot compare Trellis journal session history.`);
    } else {
      indexSessions = parseWorkspaceIndexSessions(indexFile);
    }

    const validation = validateTrellisJournalSessions({
      baselineJournalSessions,
      developerRelative,
      indexFile,
      indexSessions,
      journalSessions,
    });

    completedSessions += validation.completedSessions;
    comparedSessions += validation.comparedSessions;

    for (const message of validation.failures) {
      fail(message);
    }

  }

  if (failures.length > failureStart) {
    return;
  }

  pass(
    `checked ${completedSessions} completed Trellis journal session(s) for placeholders and validation consistency, ` +
      `${comparedSessions} journal/index commit list(s), and ${baselineSessionsCompared} baseline session(s) for historical edits.`,
  );
}

export function reviewRiskMatrix(text, extraSignals = {}) {
  return REVIEW_RISK_CATEGORY_DEFINITIONS.filter((category) => {
    if (category.patterns.some((pattern) => pattern.test(text))) {
      return true;
    }
    const categorySignals = Array.isArray(extraSignals?.[category.id]) ? extraSignals[category.id] : [];
    return categorySignals.some((signal) => typeof signal === 'string' && text.includes(signal));
  }).map((category) => ({
    id: category.id,
    label: category.label,
    variants: { ...category.variants },
  }));
}

export function reviewRiskCategories(text, extraSignals = {}) {
  return reviewRiskMatrix(text, extraSignals).map((category) => category.id);
}

function boundedReviewRiskPaths(paths) {
  const visible = paths.slice(0, MAX_REVIEW_RISK_PATHS);
  const hiddenCount = paths.length - visible.length;
  return hiddenCount > 0 ? `${visible.join(', ')} (+${hiddenCount} more)` : visible.join(', ');
}

export function isBoundaryRiskReviewPath(path) {
  const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
  if (
    (!REVIEW_CODE_PATH_PATTERN.test(normalized) && !REVIEW_WORKFLOW_PATH_PATTERN.test(normalized)) ||
    copiedTemplateKind(normalized) ||
    GENERATED_REVIEW_PATHS.has(normalized)
  ) {
    return false;
  }

  const segments = normalized.split('/');
  const basename = segments.pop() || '';
  if (segments.some((segment) => NON_PRODUCTION_CODE_DIRECTORY_SEGMENTS.has(segment))) {
    return false;
  }

  if (REVIEW_WORKFLOW_PATH_PATTERN.test(normalized)) {
    return true;
  }

  const stem = basename.replace(REVIEW_CODE_PATH_PATTERN, '');
  return !(
    stem.startsWith('test_') ||
    stem.endsWith('_test') ||
    stem.endsWith('.test') ||
    stem.endsWith('.spec')
  );
}

export function trellisTaskDirectory(path) {
  const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
  const match = /^\.trellis\/tasks\/((?:archive\/[^/]+\/[^/]+)|[^/]+)(?:\/|$)/.exec(normalized);
  return match ? `.trellis/tasks/${match[1]}` : '';
}

export function isSourceReviewPath(path) {
  const normalized = normalizePathSeparators(path).replace(/^\.\//, '');
  return (
    !copiedTemplateKind(normalized) &&
    !normalized.startsWith('.trellis/tasks/') &&
    !normalized.startsWith('.trellis/workspace/') &&
    !GENERATED_REVIEW_PATHS.has(normalized)
  );
}

function checkReviewRiskSweep() {
  const changed = currentChangedPaths();
  if (!changed) {
    warn('could not read changed paths; first-review risk sweep skipped.');
    return;
  }

  const codePaths = changed.paths.filter(isBoundaryRiskReviewPath);
  if (codePaths.length === 0) {
    pass('no changed code paths require a first-review boundary-risk sweep.');
    return;
  }

  const addedCode = currentAddedCodeText(codePaths);
  if (addedCode.oversizedUntrackedPaths.length > 0) {
    warn(
      `first-review boundary-risk content scan skipped ${addedCode.oversizedUntrackedPaths.length} oversized untracked code file(s) above ` +
        `${config.untrackedFileReadLimitBytes} bytes: ${boundedReviewRiskPaths(addedCode.oversizedUntrackedPaths)}`,
    );
  }
  if (addedCode.unreadableUntrackedPaths.length > 0) {
    warn(
      `first-review boundary-risk content scan skipped ${addedCode.unreadableUntrackedPaths.length} unreadable untracked code file(s): ` +
        boundedReviewRiskPaths(addedCode.unreadableUntrackedPaths),
    );
  }
  const matrix = reviewRiskMatrix(addedCode.text, config.reviewRiskCategorySignals);
  if (matrix.length === 0) {
    if (addedCode.oversizedUntrackedPaths.length === 0 && addedCode.unreadableUntrackedPaths.length === 0) {
      pass(`checked ${codePaths.length} changed code path(s); no boundary-risk trigger was added.`);
    }
    return;
  }

  const matrixText = matrix.map(
    (category) =>
      `${category.id} (${category.label}): good=${category.variants.good}; ` +
      `base=${category.variants.base}; failure=${category.variants.failure}`,
  ).join(' | ');
  warn(
    `changed code adds boundary-risk categories ${matrix.map((category) => category.id).join(', ')}; ` +
      `before the first remote review, cover or disposition this regression matrix: ${matrixText}`,
  );
}

function checkDiffSize() {
  const diff = currentReviewDiffStats();

  if (!diff) {
    warn('could not read git diff stats; PR-size warning skipped.');
    return;
  }

  if (diff.files.length === 0) {
    pass('no current diff to size-check.');
    return;
  }

  const changedLines = diff.files.reduce((total, file) => total + file.added + file.deleted, 0);

  if (diff.files.length > config.copilotReviewFileLimit) {
    warn(
      `${diff.label} changes ${diff.files.length} files, above GitHub Copilot's ` +
        `${config.copilotReviewFileLimit}-file review limit; Copilot will not review this diff. ` +
        'Split the change before requesting remote review.',
    );
  } else {
    pass(
      `${diff.label} changes ${diff.files.length} file(s), at or below GitHub Copilot's ` +
        `${config.copilotReviewFileLimit}-file review limit.`,
    );
  }

  if (changedLines > config.diffSizeWarningLines) {
    warn(`${diff.label} changes ${changedLines} lines; Copilot may skip review above roughly ${config.diffSizeWarningLines} changed lines.`);
  } else {
    pass(`${diff.label} changes ${changedLines} line(s), below the Copilot review-size warning threshold.`);
  }

  const largeFiles = diff.files.filter((file) => file.added + file.deleted > config.largeFileWarningLines);

  for (const file of largeFiles) {
    warn(`${diff.label} includes a large file diff (${file.added + file.deleted} lines): ${file.path}`);
  }

  const sourceFiles = diff.files.filter((file) => isSourceReviewPath(file.path));
  const sourceLines = sourceFiles.reduce((total, file) => total + file.added + file.deleted, 0);
  if (sourceLines > config.sourceReviewWarningLines) {
    warn(
      `${diff.label} changes ${sourceLines} authored source line(s) across ${sourceFiles.length} file(s); ` +
        'split the PR or record focused first-review risk evidence before requesting remote review.',
    );
  } else {
    pass(`${diff.label} changes ${sourceLines} authored source line(s), below the focused-review warning threshold.`);
  }

  const taskDirectories = new Set(diff.files.map((file) => trellisTaskDirectory(file.path)).filter(Boolean));
  if (taskDirectories.size > 1) {
    warn(
      `${diff.label} changes ${taskDirectories.size} Trellis task directories; ` +
        'confirm they form one reviewable outcome or split the work before remote review.',
    );
  } else {
    pass(`${diff.label} changes at most one Trellis task directory.`);
  }
}

function checkScopeAdvisory() {
  // Soft signal: shell out to the pack's scope classifier in advisory mode so
  // the required PR-body scope section is named while it can still be added.
  // The script resolves the PR body when it can, so this stays silent once the
  // body already carries the section and still fires before a PR exists. All
  // file-classification and heading policy lives in the bash script; this only
  // surfaces its warning and never fails the preflight.
  const ambient = process.env.SD_AI_COMMAND_PACK_SCOPE_CHECK;
  if (ambient && /^(0|false|FALSE|no|NO|skip|none|off|OFF|disabled|DISABLED)$/.test(ambient)) {
    return;
  }
  const script = resolve(scriptDir, 'sd-ai-command-pack-review-scope.sh');
  if (!existsSync(script)) {
    return;
  }
  const result = spawnSync('bash', [script], {
    cwd: rootDir,
    encoding: 'utf8',
    maxBuffer: GIT_MAX_BUFFER_BYTES,
    // The script may call `gh pr view`, and this whole path is synchronous down
    // to `make check`. Non-fatal is not enough on its own: without a bound, a
    // stalled gh would stall the gate no matter how its exit status is treated.
    timeout: 10_000,
    killSignal: 'SIGKILL',
    env: { ...process.env, SD_AI_COMMAND_PACK_SCOPE_CHECK: 'advisory' },
  });
  if (result.error) {
    // Advisory only: a missing bash, a spawn failure, or an expired timeout
    // must not fail the gate. Node sets result.error on timeout expiry.
    return;
  }
  const output = `${result.stdout || ''}${result.stderr || ''}`;
  // Match the stable machine marker, not the human wording, so the bash
  // advisory text can change without silently dropping this warning.
  const marker = 'sd-ai-command-pack-scope-advisory: ';
  const advisoryLine = output.split('\n').find((line) => line.includes(marker));
  if (advisoryLine) {
    warn(advisoryLine.slice(advisoryLine.indexOf(marker) + marker.length).trim());
  }
}

export function summarizeCopiedTemplateDiff(paths, options = {}) {
  const copied = paths.filter(isCopiedTemplatePath);
  const integration = paths.filter((path) => isRepoOwnedCopiedTemplateIntegrationPath(path, options.integrationPaths));

  return { copied, integration };
}

export function isCopiedTemplatePath(path) {
  return copiedTemplateKind(path) !== null;
}

export function copiedTemplateKind(path) {
  const normalized = normalizePathSeparators(path);

  if (isTrellisCopiedPath(normalized)) {
    return 'trellis';
  }

  if (isSdCommandPackCopiedPath(normalized)) {
    return 'sd-ai-command-pack';
  }

  return null;
}

function isTrellisCopiedPath(path) {
  return (
    path === '.trellis/.template-hashes.json' ||
    path === '.trellis/.version' ||
    path.startsWith('.trellis/scripts/') ||
    path.startsWith('.trellis/agents/') ||
    /^\.(agent|agents|claude|codebuddy|codex|cursor|devin|factory|gemini|github|kiro|kilocode|opencode|pi|qoder|reasonix|trae|zcode)\/skills\/trellis-[^/]+\//.test(path) ||
    /^\.github\/agents\/trellis-[^/]+\.agent\.md$/.test(path) ||
    path === '.github/prompts/continue.prompt.md' ||
    path === '.github/prompts/finish-work.prompt.md' ||
    path.startsWith('.github/copilot/hooks/') ||
    path === '.github/hooks/trellis.json' ||
    /^\.(claude|codebuddy|cursor|gemini|opencode|pi|qoder|trae|zcode)\/agents\/trellis-[^/]+\.md$/.test(path) ||
    /^\.zcode\/cli\/agents\/trellis-[^/]+\.md$/.test(path) ||
    /^\.factory\/droids\/trellis-[^/]+\.md$/.test(path) ||
    /^\.kiro\/agents\/trellis[^/]*\.json$/.test(path) ||
    path.startsWith('.claude/commands/trellis/') ||
    path.startsWith('.codebuddy/commands/trellis/') ||
    /^\.cursor\/commands\/trellis-[^/]+\.md$/.test(path) ||
    /^\.devin\/workflows\/trellis-[^/]+\.md$/.test(path) ||
    path.startsWith('.factory/commands/trellis/') ||
    path.startsWith('.gemini/commands/trellis/') ||
    /^\.kilocode\/workflows\/(start|continue|finish-work)\.md$/.test(path) ||
    /^\.agent\/workflows\/(start|continue|finish-work)\.md$/.test(path) ||
    path.startsWith('.opencode/commands/trellis/') ||
    /^\.pi\/prompts\/trellis-[^/]+\.md$/.test(path) ||
    /^\.qoder\/commands\/trellis-[^/]+\.md$/.test(path) ||
    /^\.trae\/commands\/trellis-[^/]+\.md$/.test(path) ||
    path.startsWith('.zcode/commands/trellis/') ||
    path === '.claude/settings.json' ||
    path.startsWith('.claude/hooks/') ||
    path.startsWith('.codebuddy/hooks/') ||
    path === '.codebuddy/settings.json' ||
    path.startsWith('.factory/hooks/') ||
    path === '.factory/settings.json' ||
    path.startsWith('.gemini/hooks/') ||
    path === '.gemini/settings.json' ||
    path.startsWith('.kiro/hooks/') ||
    path.startsWith('.opencode/lib/') ||
    path.startsWith('.opencode/plugins/') ||
    path === '.opencode/package.json' ||
    path.startsWith('.pi/extensions/trellis/') ||
    path === '.pi/settings.json' ||
    path.startsWith('.qoder/hooks/') ||
    path === '.qoder/settings.json' ||
    path.startsWith('.trae/hooks/') ||
    path === '.trae/hooks.json'
  );
}

function isSdCommandPackCopiedPath(path) {
  return (
    packInstalledTargets().has(path) ||
    path === '.sd-ai-command-pack/installed-targets.txt' ||
    path === '.sd-ai-command-pack/manifest.json' ||
    path === '.sd-ai-command-pack/provenance.json' ||
    config.copiedTemplateExtraPaths.includes(path) ||
    /^\.(agent|agents|claude|codebuddy|codex|cursor|devin|factory|gemini|github|kiro|kilocode|opencode|pi|qoder|reasonix|trae|zcode)\/skills\/sd-[^/]+\//.test(path) ||
    /^\.agent\/workflows\/sd-[^/]+\.md$/.test(path) ||
    path.startsWith('.claude/commands/sd/') ||
    path.startsWith('.codebuddy/commands/sd/') ||
    /^\.cursor\/commands\/sd-[^/]+\.md$/.test(path) ||
    /^\.devin\/workflows\/sd-[^/]+\.md$/.test(path) ||
    path.startsWith('.factory/commands/sd/') ||
    /^\.github\/prompts\/sd-[^/]+\.prompt\.md$/.test(path) ||
    path === '.github/copilot-instructions.md' ||
    path.startsWith('.gemini/commands/sd/') ||
    /^\.kilocode\/workflows\/sd-[^/]+\.md$/.test(path) ||
    /^\.opencode\/commands\/sd-[^/]+\.md$/.test(path) ||
    /^\.pi\/prompts\/sd-[^/]+\.md$/.test(path) ||
    /^\.qoder\/commands\/sd-[^/]+\.md$/.test(path) ||
    /^\.trae\/commands\/sd-[^/]+\.md$/.test(path) ||
    path.startsWith('.zcode/commands/sd/') ||
    path.startsWith('scripts/sd-ai-command-pack-') ||
    path === 'scripts/sd-ai-command-pack-review-scope.sh' ||
    path === 'scripts/trellis-full-check.sh' ||
    path === 'scripts/trellis-housekeeping.sh' ||
    path === '.gito/config.toml' ||
    path === '.gito/sd-ai-command-pack.env' ||
    path === '.prism/rules.json' ||
    path === 'docs/SD_AI_COMMAND_PACK.md' ||
    path === 'docs/TRELLIS_REVIEW_PR_PACK.md'
  );
}

function packInstalledTargets() {
  if (installedTargetsCache !== undefined) {
    return installedTargetsCache;
  }

  const file = '.sd-ai-command-pack/installed-targets.txt';

  if (!exists(file)) {
    installedTargetsCache = new Set();
    return installedTargetsCache;
  }

  installedTargetsCache = new Set(
    readText(file)
      .split('\n')
      .map((line) => normalizePathSeparators(line.trim()))
      .filter(Boolean),
  );
  return installedTargetsCache;
}

function isRepoOwnedCopiedTemplateIntegrationPath(path, integrationPaths = config.integrationPaths) {
  const normalized = normalizePathSeparators(path);
  return integrationPaths.some((pattern) => matchesPathPattern(normalized, normalizePathSeparators(pattern)));
}

export function findMissingDocumentationPathReferences(file, text, existsPath, options = {}) {
  const missing = [];
  const seen = new Set();

  for (const reference of extractDocumentationPathReferences(file, text, options)) {
    const resolved = resolveDocumentationReference(file, reference.target, reference.kind, options);

    if (!resolved) {
      continue;
    }

    const key = `${reference.line}:${resolved}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);

    if (!existsPath(resolved) && !resolvesToLineSuffixedPath(resolved, existsPath)) {
      missing.push({
        ...reference,
        resolved,
      });
    }
  }

  return missing;
}

export function maskGeneratedDocumentationPathProvenance(file, text) {
  if (file !== REVIEW_LEARNINGS_PATH_PROVENANCE_FILE) {
    return text;
  }

  return text.replace(
    REVIEW_LEARNINGS_MANAGED_BLOCK_PATTERN,
    (block) => block.replace(/[^\n]/g, ' '),
  );
}

function resolvesToLineSuffixedPath(resolved, existsPath) {
  // Documentation commonly cites line anchors — `path.md:42`, `path:12-34`,
  // `path:12:5`, `path:12-34:5`, `path:1-2,3-4`, `path:~145` — so a target with
  // trailing line/column suffixes (including comma-joined multi-ranges and `~`
  // approximate markers) resolves against its base path. Files literally named
  // with `:digits` were already matched by the direct existence check above.
  const base = resolved.replace(/(?::~?\d+(?:-\d+)?(?:,~?\d+(?:-\d+)?)*)+$/, '');
  return base !== resolved && existsPath(base);
}

export function extractDocumentationPathReferences(file, text, options = {}) {
  const references = [];
  const markdownLinkPattern = /!?\[[^\]\n]*\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g;
  const codeSpanPattern = /`([^`\n]+)`/g;

  for (const match of text.matchAll(markdownLinkPattern)) {
    const target = normalizeDocumentationReference(match[1]);

    if (target && shouldCheckDocumentationPathReference(target, 'markdown-link', options)) {
      references.push({
        file,
        kind: 'markdown-link',
        line: lineNumberAt(text, match.index ?? 0),
        target,
      });
    }
  }

  for (const match of text.matchAll(codeSpanPattern)) {
    const target = normalizeDocumentationReference(match[1]);

    if (target && shouldCheckDocumentationPathReference(target, 'code-span', options)) {
      references.push({
        file,
        kind: 'code-span',
        line: lineNumberAt(text, match.index ?? 0),
        target,
      });
    }
  }

  return references;
}

export function shouldCheckDocumentationPathReference(target, kind = 'code-span', options = {}) {
  const normalized = target.replace(/^\.\//, '');
  const referencePrefixes = options.referencePrefixes || config.referencePrefixes;
  const topLevelFiles = new Set(options.topLevelReferenceFiles || config.topLevelReferenceFiles);
  const optionalCandidatePaths = new Set(options.optionalReferencePaths || config.optionalReferencePaths);
  const ignoredPrefixes = options.ignoredReferencePrefixes || config.ignoredReferencePrefixes;

  if (
    !target ||
    target.startsWith('#') ||
    target.startsWith('/') ||
    target.startsWith('~') ||
    target.startsWith('$') ||
    target.startsWith('@') ||
    target.endsWith('/') ||
    target.includes('://') ||
    URI_SCHEME_PATTERN.test(target) ||
    /[<>{}\[\]*]/.test(target) ||
    /[\s|]/.test(target)
  ) {
    return false;
  }

  if (
    ignoredPrefixes.some((prefix) => target.startsWith(prefix)) ||
    /^\.env(?:\.|$)/.test(target)
  ) {
    return false;
  }

  if (/^[A-Z_][A-Z0-9_]*$/.test(target) || target.startsWith('--')) {
    return false;
  }

  if (optionalCandidatePaths.has(normalized)) {
    return false;
  }

  if (kind === 'markdown-link' && (target.startsWith('./') || target.startsWith('../'))) {
    return true;
  }

  if (topLevelFiles.has(normalized)) {
    return true;
  }

  return referencePrefixes.some((prefix) => normalized.startsWith(prefix));
}

function normalizeDocumentationReference(raw) {
  const trimmed = raw
    .trim()
    .replace(/^<|>$/g, '')
    .replace(/[.,;:]+$/g, '');

  if (!trimmed) {
    return '';
  }

  // Strip URL fragments and pytest-style node-id selectors
  // (tests/test_x.py::test_case) so only the file part is resolved.
  return trimmed.split('#')[0].split('::')[0];
}

function resolveDocumentationReference(file, target, kind, options = {}) {
  if (!shouldCheckDocumentationPathReference(target, kind, options)) {
    return null;
  }

  if (kind === 'markdown-link' && (target.startsWith('./') || target.startsWith('../'))) {
    const absolute = resolve(rootDir, dirname(file), target);
    const resolved = normalizePathSeparators(relative(rootDir, absolute));
    if (resolved.startsWith('../')) {
      return null;
    }
    return resolved;
  }

  return normalizePathSeparators(target.replace(/^\.\//, ''));
}

function documentationGuardFiles() {
  if (documentationGuardFilesCache !== undefined) {
    return documentationGuardFilesCache;
  }

  const files = [];

  for (const root of config.documentationRoots) {
    const absolute = resolve(rootDir, root);
    if (!exists(root)) {
      continue;
    }

    const candidates = statSync(absolute).isDirectory() ? listFiles(absolute).map(absoluteToRelative) : [root];
    for (const file of candidates) {
      if (config.documentationExtensions.some((extension) => file.endsWith(extension))) {
        files.push(file);
      }
    }
  }

  documentationGuardFilesCache = [...new Set(files)].sort();
  return documentationGuardFilesCache;
}

// Runs git with an explicit output ceiling. A nonzero exit status is the
// caller's decision (many call sites legitimately tolerate absent refs or
// diffs), but result.error means git never ran or its output was cut off,
// so tolerating it would silently degrade to an empty diff — throw instead
// and let runCheck turn it into a hard failure.
function runGit(args) {
  const result = spawnSync('git', args, {
    cwd: rootDir,
    encoding: 'utf8',
    maxBuffer: GIT_MAX_BUFFER_BYTES,
  });

  if (result.error) {
    throw new GitCommandError(`git ${args.join(' ')} could not run: ${result.error.message}`);
  }

  if (result.signal || result.status === null) {
    const reason = result.signal
      ? `terminated by signal ${result.signal}`
      : 'exited without a status';
    throw new GitCommandError(`git ${args.join(' ')} did not complete: ${reason}`);
  }

  return result;
}

function gitStdout(args) {
  const result = runGit(args);

  if (result.status !== 0) {
    return '';
  }

  return result.stdout.trim();
}

function gitFileAtRef(ref, file) {
  const result = runGit(['show', `${ref}:${file}`]);
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout).trim() || `exit status ${result.status}`;
    throw new GitCommandError(`git show ${ref}:${file} failed: ${detail}`);
  }
  return result.stdout;
}

function gitFilesAtRef(ref, directory) {
  const result = runGit(['ls-tree', '-r', '--name-only', '-z', ref, '--', directory]);
  if (result.status !== 0) {
    const detail = (result.stderr || result.stdout).trim() || `exit status ${result.status}`;
    throw new GitCommandError(`git ls-tree ${ref} -- ${directory} failed: ${detail}`);
  }
  return result.stdout.split('\0').filter(Boolean);
}

function gitRefExists(ref) {
  if (!ref || ref.startsWith('-')) {
    return false;
  }
  return spawnSync('git', ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`], {
    cwd: rootDir,
    encoding: 'utf8',
  }).status === 0;
}

function configuredReviewBaseRef(name) {
  const ref = process.env[name];
  if (!ref) {
    return '';
  }
  if (gitRefExists(ref)) {
    return ref;
  }
  warn(`${name}=${ref} does not resolve to a commit; falling back to discovered default branch.`);
  return '';
}

function trellisRootDefaultBranchName() {
  // The repository default branch for the root-task base_branch rule. This is
  // deliberately NOT defaultReviewBaseRef(): that resolver answers "what do I
  // diff against" and may legitimately return a stacked-PR feature base (env
  // override), the current branch's upstream, or an arbitrary sorted remote
  // ref — none of which is a statement about the repository default.
  const configured = (process.env.SD_AI_COMMAND_PACK_DEFAULT_BRANCH || '').trim();
  if (configured) {
    return configured;
  }
  const originHead = gitStdout(['symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD']);
  if (gitRefExists(originHead)) {
    return originHead.replace(/^[^/]+\//, '');
  }
  return '';
}

function defaultReviewBaseRef() {
  const configured = configuredReviewBaseRef('SD_AI_COMMAND_PACK_REVIEW_PREFLIGHT_BASE_REF')
    || configuredReviewBaseRef('SD_AI_COMMAND_PACK_FULL_CHECK_BASE_REF');
  if (configured) {
    return configured;
  }

  const originHead = gitStdout(['symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD']);
  if (gitRefExists(originHead)) {
    return originHead;
  }

  const upstream = gitStdout(['rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}']);
  if (gitRefExists(upstream)) {
    return upstream;
  }

  const remoteRefs = gitStdout(['for-each-ref', '--format=%(refname:short)', 'refs/remotes'])
    .split('\n')
    .map((ref) => ref.trim())
    .filter((ref) => ref && !ref.endsWith('/HEAD') && gitRefExists(ref))
    .sort();

  return remoteRefs[0] || '';
}

function journalBaselineRef() {
  const baseRef = defaultReviewBaseRef();
  if (baseRef) {
    return baseRef;
  }
  return gitRefExists('HEAD') ? 'HEAD' : '';
}

function currentDiffSources(...kindArgs) {
  const baseRef = defaultReviewBaseRef();
  const sources = [
    { args: ['diff', ...kindArgs, '--cached'], label: 'staged diff' },
  ];

  if (baseRef) {
    sources.push({ args: ['diff', ...kindArgs, `${baseRef}...HEAD`], label: 'branch diff' });
  }

  sources.push({ args: ['diff', ...kindArgs], label: 'working tree diff' });
  return sources;
}

function reviewBaselineRef() {
  const baseRef = defaultReviewBaseRef();
  if (!baseRef) {
    return gitRefExists('HEAD') ? 'HEAD' : '';
  }
  if (!gitRefExists('HEAD')) {
    return baseRef;
  }

  const mergeBase = gitStdout(['merge-base', baseRef, 'HEAD']);
  if (gitRefExists(mergeBase)) {
    return mergeBase;
  }

  warn(`could not resolve the merge base of ${baseRef} and HEAD; falling back to ${baseRef}.`);
  return baseRef;
}

function currentUntrackedPaths() {
  const result = runGit(['ls-files', '--others', '--exclude-standard', '-z']);
  if (result.status !== 0) {
    return [];
  }
  return result.stdout.split('\0').filter(Boolean);
}

function textLineCount(text) {
  if (!text) {
    return 0;
  }
  const lines = text.split(/\r?\n/);
  return lines.at(-1) === '' ? lines.length - 1 : lines.length;
}

function currentReviewDiffStats() {
  const baseline = reviewBaselineRef();
  const args = ['diff', '--numstat', '-z'];
  if (baseline) {
    args.push(baseline);
  }
  args.push('--');

  const result = runGit(args);
  if (result.status !== 0) {
    return null;
  }

  const files = parseNumstat(result.stdout);
  const seen = new Set(files.map((file) => file.path));
  for (const path of currentUntrackedPaths()) {
    if (seen.has(path)) {
      continue;
    }
    const added = untrackedAddedLineEstimate(path);
    if (added === null) {
      continue;
    }
    files.push({ added, deleted: 0, path });
  }

  return {
    args,
    label: baseline ? `${baseline} to working tree` : 'working tree diff',
    files,
  };
}

function untrackedAddedLineEstimate(path) {
  const content = boundedUntrackedFileText(path);
  if (!content) {
    return null;
  }

  if (content.status === 'oversized') {
    return Math.max(config.largeFileWarningLines + 1, 1);
  }

  if (content.status === 'unreadable') {
    return null;
  }

  return textLineCount(content.text);
}

function boundedUntrackedFileText(path) {
  let pathEntry;
  try {
    pathEntry = lstatSync(resolve(rootDir, path));
  } catch {
    return null;
  }
  if (!pathEntry.isFile()) {
    return null;
  }
  if (pathEntry.size > config.untrackedFileReadLimitBytes) {
    return { status: 'oversized', text: '' };
  }

  let descriptor;
  try {
    const noFollowFlag = typeof fsConstants.O_NOFOLLOW === 'number' ? fsConstants.O_NOFOLLOW : 0;
    descriptor = openSync(resolve(rootDir, path), fsConstants.O_RDONLY | noFollowFlag);
    const openedEntry = fstatSync(descriptor);
    if (
      !openedEntry.isFile() ||
      openedEntry.dev !== pathEntry.dev ||
      openedEntry.ino !== pathEntry.ino
    ) {
      return { status: 'unreadable', text: '' };
    }
    if (openedEntry.size > config.untrackedFileReadLimitBytes) {
      return { status: 'oversized', text: '' };
    }

    const buffer = Buffer.alloc(openedEntry.size);
    let bytesRead = 0;
    while (bytesRead < buffer.length) {
      const count = readSync(descriptor, buffer, bytesRead, buffer.length - bytesRead, bytesRead);
      if (count === 0) {
        break;
      }
      bytesRead += count;
    }
    return {
      status: 'read',
      text: new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, bytesRead)),
    };
  } catch {
    return { status: 'unreadable', text: '' };
  } finally {
    if (descriptor !== undefined) {
      try {
        closeSync(descriptor);
      } catch {
        // The scan is advisory; a failed close must not hide its result.
      }
    }
  }
}

function addedLinesFromDiff(output) {
  return output
    .split(/\r?\n/)
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
    .join('\n');
}

function currentAddedCodeText(codePaths) {
  const baseline = reviewBaselineRef();
  const outputs = [];
  const oversizedUntrackedPaths = [];
  const unreadableUntrackedPaths = [];
  const diffArgs = ['diff', '--unified=0', '--no-ext-diff', '--no-color'];
  if (baseline) {
    const result = runGit([...diffArgs, baseline, '--', ...codePaths]);
    if (result.status === 0) {
      outputs.push(addedLinesFromDiff(result.stdout));
    }
  } else {
    for (const extraArgs of [['--cached'], []]) {
      const result = runGit([...diffArgs, ...extraArgs, '--', ...codePaths]);
      if (result.status === 0) {
        outputs.push(addedLinesFromDiff(result.stdout));
      }
    }
  }

  const untracked = new Set(currentUntrackedPaths());
  for (const path of codePaths) {
    if (!untracked.has(path)) {
      continue;
    }
    const content = boundedUntrackedFileText(path);
    if (!content) {
      continue;
    }
    if (content.status === 'oversized') {
      oversizedUntrackedPaths.push(path);
    } else if (content.status === 'unreadable') {
      unreadableUntrackedPaths.push(path);
    } else {
      outputs.push(content.text);
    }
  }

  return { text: outputs.join('\n'), oversizedUntrackedPaths, unreadableUntrackedPaths };
}

function currentChangedPaths() {
  const sources = [
    ...currentDiffSources('--name-only'),
    { args: ['ls-files', '--others', '--exclude-standard'], label: 'untracked files' },
  ];
  const paths = new Set();
  const labels = [];
  let inspected = false;

  for (const source of sources) {
    const result = runGit(source.args);

    if (result.status !== 0) {
      continue;
    }

    inspected = true;
    const sourcePaths = result.stdout
      .trim()
      .split('\n')
      .map((path) => path.trim())
      .filter(Boolean);

    if (sourcePaths.length > 0) {
      labels.push(source.label);
      for (const path of sourcePaths) {
        paths.add(path);
      }
    }
  }

  if (!inspected) {
    return null;
  }

  return {
    args: sources.flatMap((source) => source.args),
    label: labels.length > 0 ? labels.join(' + ') : 'current diff',
    paths: [...paths],
  };
}

export function parseNumstat(output) {
  if (output.includes('\0')) {
    return parseNumstatZ(output);
  }

  return output
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((line) => {
      const [addedRaw, deletedRaw, ...pathParts] = line.split('\t');

      return {
        added: Number.isFinite(Number(addedRaw)) ? Number(addedRaw) : 0,
        deleted: Number.isFinite(Number(deletedRaw)) ? Number(deletedRaw) : 0,
        path: pathParts.join('\t'),
      };
    });
}

function parseNumstatZ(output) {
  const tokens = output.split('\0').filter((token) => token !== '');
  const files = [];

  for (let index = 0; index < tokens.length; index += 1) {
    const fields = tokens[index].split('\t');
    const addedRaw = fields[0];
    const deletedRaw = fields[1];
    let path = fields.slice(2).join('\t');

    if (!path && index + 1 < tokens.length) {
      const oldPath = tokens[index + 1];
      const newPath = tokens[index + 2] || oldPath;
      path = newPath;
      index += tokens[index + 2] ? 2 : 1;
    }

    files.push({
      added: Number.isFinite(Number(addedRaw)) ? Number(addedRaw) : 0,
      deleted: Number.isFinite(Number(deletedRaw)) ? Number(deletedRaw) : 0,
      path,
    });
  }

  return files;
}

export function validateTrellisJournalSessions({
  baselineJournalSessions = [],
  developerRelative,
  indexFile,
  indexSessions,
  journalSessions,
}) {
  if (!developerRelative) {
    developerRelative = dirname((journalSessions || [])[0]?.file || '.');
  }

  const validationFailures = [];
  const sessions = new Map();
  const baselineSessions = new Map(
    baselineJournalSessions.map((session) => [session.number, session]),
  );
  let completedSessions = 0;
  let comparedSessions = 0;

  for (const session of journalSessions) {
    if (sessions.has(session.number)) {
      validationFailures.push(`${session.file}:${session.startLine} duplicates Session ${session.number} in ${developerRelative}.`);
      continue;
    }

    sessions.set(session.number, session);

    if (!session.completed) {
      continue;
    }

    completedSessions += 1;

    for (const placeholder of ['(Add details)', '(Add test results)']) {
      for (const index of findStringIndexes(session.content, placeholder)) {
        const line = session.startLine + lineNumberAt(session.content, index) - 1;
        validationFailures.push(`${session.file}:${line} completed Session ${session.number} still contains placeholder ${placeholder}.`);
      }
    }

    const baselineSession = baselineSessions.get(session.number);
    const unchangedFromBaseline =
      baselineSession &&
      normalizeJournalSessionContent(baselineSession.content) ===
        normalizeJournalSessionContent(session.content);
    if (!unchangedFromBaseline) {
      for (const fallback of findContradictoryJournalValidationFallbacks(session)) {
        validationFailures.push(
          `${session.file}:${fallback.line} completed Session ${session.number} claims successful validation in Summary or Main Changes, ` +
            `but Testing still says "${fallback.text}"; record concrete validation evidence or remove the positive validation claim.`,
        );
      }
    }
  }

  if (!indexSessions) {
    return { comparedSessions, completedSessions, failures: validationFailures };
  }

  for (const session of sessions.values()) {
    const indexSession = indexSessions.get(session.number);

    if (!indexSession) {
      validationFailures.push(`${indexFile} is missing Session ${session.number} from ${session.file}:${session.startLine}.`);
      continue;
    }

    comparedSessions += 1;

    if (!sameStringArray(session.commits, indexSession.commits)) {
      validationFailures.push(
        `${indexFile}:${indexSession.line} Session ${session.number} commits ` +
          `${formatCommitList(indexSession.commits)} do not match ${session.file}:${session.startLine} ` +
          `${formatCommitList(session.commits)}.`,
      );
    }
  }

  for (const indexSession of indexSessions.values()) {
    if (!sessions.has(indexSession.number)) {
      validationFailures.push(`${indexFile}:${indexSession.line} lists Session ${indexSession.number}, but no matching journal entry exists.`);
    }
  }

  return { comparedSessions, completedSessions, failures: validationFailures };
}

function parseJournalSessions(file) {
  return parseJournalSessionsFromText(file, readText(file));
}

export function parseJournalSessionsFromText(file, text) {
  const matches = [...text.matchAll(/^## Session\s+(\d+):\s*(.+)$/gm)];

  return matches.map((match, index) => {
    const start = match.index ?? 0;
    const end = matches[index + 1]?.index ?? text.length;
    const content = text.slice(start, end);
    const status = extractMarkdownSection(content, 'Status');

    return {
      file,
      number: Number(match[1]),
      title: match[2].trim(),
      content,
      startLine: lineNumberAt(text, start),
      completed: /^\s*(?:[-*]\s*)?(?:\[OK\]\s*)?\*\*Completed\*\*\s*$/im.test(status),
      commits: extractCommitHashes(extractMarkdownSection(content, 'Git Commits')),
    };
  });
}

export function findContradictoryJournalValidationFallbacks(session) {
  if (!session?.completed || !hasPositiveJournalValidationClaim(session.content)) {
    return [];
  }

  const testing = extractMarkdownSectionRange(session.content, 'Testing');
  if (!testing) {
    return [];
  }

  return [...testing.content.matchAll(JOURNAL_VALIDATION_FALLBACK_PATTERN)].map((match) => ({
    line:
      session.startLine +
      lineNumberAt(session.content, testing.startIndex + (match.index ?? 0)) -
      1,
    text: match[1],
  }));
}

function hasPositiveJournalValidationClaim(content) {
  return ['Summary', 'Main Changes'].some((heading) => {
    const section = extractMarkdownSectionRange(content, heading);
    if (!section) {
      return false;
    }

    return section.content.split(/\r?\n/).some((line) => {
      const claim = line.replace(/\bno failures?\b/gi, '');
      return (
        JOURNAL_VALIDATION_SURFACE_PATTERN.test(claim) &&
        JOURNAL_VALIDATION_SUCCESS_PATTERN.test(claim) &&
        !JOURNAL_VALIDATION_NEGATION_PATTERN.test(claim)
      );
    });
  });
}

export function findHistoricalTrellisJournalSessionEdits(baselineSessions, currentSessions) {
  if (baselineSessions.length === 0) {
    return [];
  }

  const currentByNumber = new Map();
  for (const session of currentSessions) {
    if (!currentByNumber.has(session.number)) {
      currentByNumber.set(session.number, session);
    }
  }

  const newestCurrentSession = currentByNumber.size > 0
    ? Math.max(...currentByNumber.keys())
    : Number.NEGATIVE_INFINITY;
  const issues = [];

  for (const baselineSession of baselineSessions) {
    const currentSession = currentByNumber.get(baselineSession.number);
    if (!currentSession) {
      issues.push({ kind: 'removed', session: baselineSession });
    } else if (
      currentSession.number < newestCurrentSession &&
      normalizeJournalSessionContent(currentSession.content) !==
        normalizeJournalSessionContent(baselineSession.content)
    ) {
      issues.push({ kind: 'modified', session: currentSession });
    }
  }

  return issues;
}

function normalizeJournalSessionContent(content) {
  return content
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.trimEnd())
    .join('\n')
    .trimEnd();
}

function parseWorkspaceIndexSessions(file) {
  return parseWorkspaceIndexSessionsFromText(file, readText(file), { onDuplicate: fail });
}

export function parseWorkspaceIndexSessionsFromText(file, text, options = {}) {
  const onDuplicate = options.onDuplicate || (() => {});
  const sessions = new Map();

  for (const match of text.matchAll(/^\|\s*(\d+)\s*\|[^|]*\|[^|]*\|\s*([^|]*?)\s*\|[^|]*\|[ \t]*$/gm)) {
    const number = Number(match[1]);
    const line = lineNumberAt(text, match.index ?? 0);

    if (sessions.has(number)) {
      const existing = sessions.get(number);
      onDuplicate(`${file}:${line} duplicates Session ${number}, already listed at ${file}:${existing.line}.`);
      continue;
    }

    sessions.set(number, {
      number,
      line,
      commits: extractCommitHashes(match[2]),
    });
  }

  return sessions;
}

function extractMarkdownSection(markdown, heading) {
  return extractMarkdownSectionRange(markdown, heading)?.content || '';
}

function extractMarkdownSectionRange(markdown, heading) {
  const headingMatch = new RegExp(`^###\\s+${escapeRegExp(heading)}\\s*$`, 'm').exec(markdown);

  if (!headingMatch) {
    return null;
  }

  const startIndex = (headingMatch.index ?? 0) + headingMatch[0].length;
  const rest = markdown.slice(startIndex);
  const nextHeading = /^###\s+/m.exec(rest);

  return {
    content: nextHeading ? rest.slice(0, nextHeading.index) : rest,
    startIndex,
  };
}

export function extractCommitHashes(text) {
  return [...text.matchAll(/\b([0-9a-f]{7,40})\b/gi)].map((match) => match[1].toLowerCase());
}

function findStringIndexes(text, value) {
  const indexes = [];
  let index = text.indexOf(value);

  while (index !== -1) {
    indexes.push(index);
    index = text.indexOf(value, index + 1);
  }

  return indexes;
}

function sameStringArray(left, right) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function formatCommitList(commits) {
  return commits.length > 0 ? commits.map((commit) => `\`${commit}\``).join(', ') : '(none)';
}

function collectNestedOverridePackages(value, path, found) {
  if (!isPlainObject(value)) {
    return;
  }

  for (const [selector, nestedValue] of Object.entries(value)) {
    if (selector === '.') {
      continue;
    }

    const packageName = packageNameFromOverrideSelector(selector);
    const locations = found.get(packageName) || [];
    locations.push(path.concat(selector).join(' > '));
    found.set(packageName, locations);

    collectNestedOverridePackages(nestedValue, path.concat(selector), found);
  }
}

function packageNameFromOverrideSelector(selector) {
  if (selector.startsWith('@')) {
    const slashIndex = selector.indexOf('/');

    if (slashIndex === -1) {
      return selector;
    }

    const versionIndex = selector.indexOf('@', slashIndex + 1);
    return versionIndex === -1 ? selector : selector.slice(0, versionIndex);
  }

  const versionIndex = selector.indexOf('@');
  return versionIndex <= 0 ? selector : selector.slice(0, versionIndex);
}

function listFiles(directory) {
  let entries;

  try {
    entries = readdirSync(directory, { withFileTypes: true });
  } catch {
    return [];
  }

  return entries.flatMap((entry) => {
    const path = resolve(directory, entry.name);

    if (entry.isDirectory()) {
      return listFiles(path);
    }

    if (!entry.isFile()) {
      return [];
    }

    try {
      statSync(path);
      return [path];
    } catch {
      return [];
    }
  });
}

function exists(file) {
  return existsSync(resolve(rootDir, file));
}

function pathEntryExists(file) {
  try {
    lstatSync(resolve(rootDir, file));
    return true;
  } catch (error) {
    return error?.code !== 'ENOENT';
  }
}

function isRegularFile(file) {
  try {
    return lstatSync(resolve(rootDir, file)).isFile();
  } catch {
    return false;
  }
}

function readJson(file) {
  return JSON.parse(readText(file));
}

function readText(file) {
  const path = resolve(rootDir, file);
  const cached = readTextCache.get(path);
  if (cached !== undefined) {
    return cached;
  }

  const text = readFileSync(path, 'utf8');
  readTextCache.set(path, text);
  return text;
}

function absoluteToRelative(file) {
  return normalizePathSeparators(relative(rootDir, file));
}

function normalizePathSeparators(path) {
  return path.replace(/\\/g, '/');
}

function matchesPathPattern(path, pattern) {
  if (pattern.endsWith('/**')) {
    const base = pattern.slice(0, -3);
    return path === base || path.startsWith(`${base}/`);
  }

  const regex = new RegExp(`^${escapeRegExp(pattern).replace(/\\\*/g, '[^/]*')}$`);
  return regex.test(path);
}

function lineNumberAt(text, index) {
  return text.slice(0, index).split('\n').length;
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function isPlainObject(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function pass(message) {
  passes.push(message);
}

function warn(message) {
  warnings.push(message);
}

function fail(message) {
  failures.push(message);
}
