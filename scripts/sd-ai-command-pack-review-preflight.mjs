#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { closeSync, constants as fsConstants, existsSync, fstatSync, lstatSync, openSync, readFileSync, readSync, readdirSync, realpathSync, statSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const defaultRootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let rootDir = defaultRootDir;
let config = defaultConfig();
let failures = [];
let warnings = [];
let passes = [];
let installedTargetsCache;
let documentationGuardFilesCache;
const readTextCache = new Map();

const URI_SCHEME_PATTERN = /^[A-Za-z][A-Za-z0-9+.-]*:/;
const MIN_NODE_VERSION = { major: 16, minor: 9, label: '16.9.0' };
// Git output ceiling for spawnSync calls that read diffs; Node's 1 MiB
// default truncates large diffs and surfaces as a spawn error.
const GIT_MAX_BUFFER_BYTES = 64 * 1024 * 1024;
const MAX_TRELLIS_TASK_LINKS = 100;
const MAX_TRELLIS_TASK_REFERENCE_LENGTH = 255;
const TRELLIS_TASK_STATUSES = new Set(['planning', 'in_progress', 'review', 'completed']);
const ACTIVE_TRELLIS_TASK_STATUSES = new Set(['planning', 'in_progress', 'review']);
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
  runCheck('completed Trellis task location', checkCompletedTrellisTaskLocation);
  runCheck('Trellis task context seeds', checkTrellisTaskContextSeeds);
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

if (isMainModule()) {
  const unsupportedNode = unsupportedNodeVersionMessage(process.version);
  if (unsupportedNode) {
    console.error(`error: ${unsupportedNode}`);
    process.exit(2);
  }

  const result = runReviewPreflight();
  printReviewPreflightResult(result);

  if (result.failures.length > 0) {
    process.exit(1);
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

export function unsupportedNodeVersionMessage(version) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)/.exec(version || '');
  if (!match) {
    return `scripts/sd-ai-command-pack-review-preflight.mjs requires Node >= ${MIN_NODE_VERSION.label}; could not parse ${version || 'unknown version'}.`;
  }

  const major = Number(match[1]);
  const minor = Number(match[2]);
  if (major > MIN_NODE_VERSION.major || (major === MIN_NODE_VERSION.major && minor >= MIN_NODE_VERSION.minor)) {
    return '';
  }

  return `scripts/sd-ai-command-pack-review-preflight.mjs requires Node >= ${MIN_NODE_VERSION.label}; current ${version}.`;
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
          '.trellis/tasks/MM-DD-name/task.json or .trellis/tasks/archive/YYYY-MM/MM-DD-name/task.json.',
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

    for (const issue of validateTrellisTaskMetadata(record, artifact.taskDir, artifact.archived)) {
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
  if (!directoryMatch) {
    issues.push('name cannot be verified because the task directory must use the MM-DD-name form');
  } else if (nameValid && record.name !== directoryMatch[1]) {
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

function loadReferencedTrellisTaskRecord(sourceFile, field, taskDirectoryName) {
  const located = locateTrellisTaskRecord(taskDirectoryName);
  if (located.error) {
    fail(`${sourceFile} field ${field} references ${taskDirectoryName}, but the record cannot be verified: ${located.error}.`);
    return null;
  }
  if (located.paths.length === 0) {
    fail(`${sourceFile} field ${field} references missing task ${taskDirectoryName}.`);
    return null;
  }
  if (located.paths.length > 1) {
    fail(
      `${sourceFile} field ${field} references ambiguous task ${taskDirectoryName}: ${located.paths.join(', ')}.`,
    );
    return null;
  }

  const referencedFile = located.paths[0];
  const loaded = loadTrellisTaskMetadataFile(referencedFile);
  if (loaded.status !== 'loaded') {
    fail(
      `${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} ${loaded.message}.`,
    );
    return null;
  }

  let record;
  try {
    record = JSON.parse(loaded.text);
  } catch (error) {
    fail(
      `${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} could not be parsed as JSON: ` +
        thrownValueMessage(error),
    );
    return null;
  }
  if (!isPlainObject(record)) {
    fail(`${sourceFile} field ${field} references ${taskDirectoryName}, but ${referencedFile} does not contain a JSON object.`);
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
    return { status: 'unsafe', message: 'is a symlink; task metadata must be a regular file' };
  }
  if (!pathEntry.isFile()) {
    return { status: 'unsafe', message: 'is not a regular file; task metadata must be a regular file' };
  }
  if (pathEntry.size > config.untrackedFileReadLimitBytes) {
    return {
      status: 'oversized',
      message: `exceeds the bounded task metadata read limit of ${config.untrackedFileReadLimitBytes} bytes`,
    };
  }

  const content = boundedUntrackedFileText(file);
  if (!content || content.status === 'unreadable') {
    return { status: 'unreadable', message: 'could not be read safely as a regular file' };
  }
  if (content.status === 'oversized') {
    return {
      status: 'oversized',
      message: `exceeds the bounded task metadata read limit of ${config.untrackedFileReadLimitBytes} bytes`,
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

function checkTrellisTaskContextSeeds() {
  const failureStart = failures.length;
  const diff = currentChangedPaths();
  if (diff === null) {
    warn('could not inspect current diff for Trellis task context seeds.');
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
            '.trellis/tasks/archive/YYYY-MM/MM-DD-name/{implement,check}.jsonl.',
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
  for (const file of contextFiles) {
    if (!isRegularFile(file)) {
      continue;
    }

    inspectedFiles += 1;
    for (const seed of findTrellisTaskContextSeedRows(file, readText(file))) {
      fail(
        `${seed.file}:${seed.line} still contains a generated _example scaffold row; ` +
          'replace it with grounded {"file": "<path>", "reason": "<why>"} context or leave the file empty.',
      );
    }
  }

  if (inspectedFiles === 0) {
    if (failures.length === failureStart) {
      pass('no changed Trellis task context files require scaffold checks.');
    }
    return;
  }

  if (failures.length === failureStart) {
    pass(`checked ${inspectedFiles} changed Trellis task context file(s) for generated _example scaffold rows.`);
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
  const match = /^\.trellis\/tasks\/((?:archive\/\d{4}-\d{2}\/\d{2}-\d{2}-[^/]+)|\d{2}-\d{2}-[^/]+)\/(task\.json|implement\.jsonl|check\.jsonl)$/.exec(normalized);
  if (!match || match[1] === 'archive') {
    return null;
  }

  return {
    taskDir: `.trellis/tasks/${match[1]}`,
    artifact: match[2],
    archived: match[1].startsWith('archive/'),
  };
}

export function findTrellisTaskContextSeedRows(file, text) {
  const seeds = [];

  for (const [index, line] of text.split(/\r?\n/).entries()) {
    if (!line.trim()) {
      continue;
    }

    let record;
    try {
      record = JSON.parse(line);
    } catch {
      continue;
    }

    if (isPlainObject(record) && Object.prototype.hasOwnProperty.call(record, '_example')) {
      seeds.push({ file, line: index + 1 });
    }
  }

  return seeds;
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
  // Author-time soft signal: shell out to the pack's scope classifier in
  // advisory mode so the required PR-body scope section is named before any
  // PR exists. All file-classification and heading policy lives in the bash
  // script; this only surfaces its warning and never fails the preflight.
  const ambient = process.env.SD_AI_COMMAND_PACK_SCOPE_CHECK;
  if (ambient && /^(0|false|FALSE|no|NO|skip|none|off|OFF|disabled|DISABLED)$/.test(ambient)) {
    return;
  }
  const script = resolve(rootDir, 'scripts', 'sd-ai-command-pack-review-scope.sh');
  if (!existsSync(script)) {
    return;
  }
  const result = spawnSync('bash', [script], {
    cwd: rootDir,
    encoding: 'utf8',
    maxBuffer: GIT_MAX_BUFFER_BYTES,
    env: { ...process.env, SD_AI_COMMAND_PACK_SCOPE_CHECK: 'advisory' },
  });
  if (result.error) {
    // Advisory only: a missing bash or spawn failure must not fail the gate.
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
    return { status: 'read', text: buffer.toString('utf8', 0, bytesRead) };
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
