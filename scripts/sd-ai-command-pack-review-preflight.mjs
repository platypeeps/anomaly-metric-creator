#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const defaultRootDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
let rootDir = defaultRootDir;
let config = defaultConfig();
let failures = [];
let warnings = [];
let passes = [];
let installedTargetsCache;

const URI_SCHEME_PATTERN = /^[A-Za-z][A-Za-z0-9+.-]*:/;

export function runReviewPreflight(options = {}) {
  rootDir = resolve(options.rootDir || defaultRootDir);
  failures = [];
  warnings = [];
  passes = [];
  installedTargetsCache = undefined;
  // Load config only after the result buffers are reset so a malformed config
  // file's fail() entry is reported instead of being wiped by the reset.
  config = loadConfig(rootDir, options.configPath);

  runCheck('package override sources of truth', checkPackageOverrides);
  runCheck('copied template diff disclosure', checkCopiedTemplateDiffDisclosure);
  runCheck('documentation path hygiene', checkDocumentationPathHygiene);
  runCheck('documentation path references', checkDocumentationPathReferences);
  runCheck('Trellis journal records', checkTrellisJournalRecords);
  runCheck('diff size warning', checkDiffSize);

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
      '.agents/',
      '.codex/',
      '.cursor/',
      '.gemini/',
      '.gito/',
      '.github/',
      '.opencode/',
      '.prism/',
      '.sd-ai-command-pack/',
      '.trellis/',
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
      '.sd-ai-command-pack/pr-body-scope.json',
      '.sd-ai-command-pack/provenance.json',
      '.sd-ai-command-pack/review-preflight.json',
      '.trellis/.developer',
      '.trellis/.template-hashes.json',
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
    diffSizeWarningLines: 20000,
    largeFileWarningLines: 5000,
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

  for (const key of ['diffSizeWarningLines', 'largeFileWarningLines']) {
    if (Number.isFinite(raw[key])) {
      merged[key] = raw[key];
    }
  }

  return merged;
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

if (import.meta.url === pathToFileURL(process.argv[1] || '').href) {
  const result = runReviewPreflight();
  printReviewPreflightResult(result);

  if (result.failures.length > 0) {
    process.exit(1);
  }
}

function runCheck(label, check) {
  try {
    check();
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error);
    fail(`${label} check crashed: ${reason}`);
  }
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
      if (Object.hasOwn(value, '.')) {
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
    if (
      file === 'docs/SD_AI_COMMAND_PACK.md' ||
      file === 'docs/repomix-map.md' ||
      file.startsWith('.trellis/tasks/archive/')
    ) {
      continue;
    }

    missing.push(...findMissingDocumentationPathReferences(file, readText(file), (candidate) => exists(candidate)));
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

function checkTrellisJournalRecords() {
  const failureStart = failures.length;
  const workspaceRoot = resolve(rootDir, '.trellis/workspace');

  if (!exists('.trellis/workspace')) {
    pass('.trellis/workspace is not present; Trellis journal checks skipped.');
    return;
  }

  const developerDirs = readdirSync(workspaceRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => resolve(workspaceRoot, entry.name))
    .sort();
  let completedSessions = 0;
  let comparedSessions = 0;

  for (const developerDir of developerDirs) {
    const developerRelative = absoluteToRelative(developerDir);
    const indexFile = `${developerRelative}/index.md`;
    const journalFiles = readdirSync(developerDir, { withFileTypes: true })
      .filter((entry) => entry.isFile() && /^journal-\d+\.md$/.test(entry.name))
      .map((entry) => `${developerRelative}/${entry.name}`)
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const journalSessions = [];

    for (const journalFile of journalFiles) {
      journalSessions.push(...parseJournalSessions(journalFile));
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

  pass(`checked ${completedSessions} completed Trellis journal session(s) for placeholders and ${comparedSessions} journal/index commit list(s).`);
}

function checkDiffSize() {
  const diff = currentDiffStats();

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
    /^\.(agents|claude|codex|cursor|gemini|github|opencode)\/skills\/trellis-[^/]+\//.test(path) ||
    /^\.github\/agents\/trellis-[^/]+\.agent\.md$/.test(path) ||
    path === '.github/prompts/continue.prompt.md' ||
    path === '.github/prompts/finish-work.prompt.md' ||
    path.startsWith('.github/copilot/hooks/') ||
    path === '.github/hooks/trellis.json' ||
    /^\.(claude|cursor|gemini|opencode)\/agents\/trellis-[^/]+\.md$/.test(path) ||
    path.startsWith('.claude/commands/trellis/') ||
    /^\.cursor\/commands\/trellis-[^/]+\.md$/.test(path) ||
    path.startsWith('.gemini/commands/trellis/') ||
    path.startsWith('.opencode/commands/trellis/') ||
    path.startsWith('.gemini/hooks/') ||
    path === '.gemini/settings.json' ||
    path.startsWith('.opencode/lib/') ||
    path.startsWith('.opencode/plugins/') ||
    path === '.opencode/package.json'
  );
}

function isSdCommandPackCopiedPath(path) {
  return (
    packInstalledTargets().has(path) ||
    path === '.sd-ai-command-pack/installed-targets.txt' ||
    path === '.sd-ai-command-pack/provenance.json' ||
    config.copiedTemplateExtraPaths.includes(path) ||
    /^\.(agents|claude|codex|cursor|gemini|github|opencode)\/skills\/sd-[^/]+\//.test(path) ||
    path.startsWith('.claude/commands/sd/') ||
    /^\.cursor\/commands\/sd-[^/]+\.md$/.test(path) ||
    /^\.github\/prompts\/sd-[^/]+\.prompt\.md$/.test(path) ||
    path === '.github/copilot-instructions.md' ||
    path.startsWith('.gemini/commands/sd/') ||
    /^\.opencode\/commands\/sd-[^/]+\.md$/.test(path) ||
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

function resolvesToLineSuffixedPath(resolved, existsPath) {
  // Documentation commonly cites line anchors — `path.md:42`, `path:12-34`,
  // `path:12:5`, `path:12-34:5` — so a target with trailing line/column
  // suffixes resolves against its base path. Files literally named with
  // `:digits` were already matched by the direct existence check above.
  const base = resolved.replace(/(?::\d+(?:-\d+)?)+$/, '');
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

  return [...new Set(files)].sort();
}

function gitStdout(args) {
  const result = spawnSync('git', args, {
    cwd: rootDir,
    encoding: 'utf8',
  });

  if (result.error || result.status !== 0) {
    return '';
  }

  return result.stdout.trim();
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

function currentDiffStats() {
  const sources = currentDiffSources('--numstat', '-z');

  for (const source of sources) {
    const result = spawnSync('git', source.args, {
      cwd: rootDir,
      encoding: 'utf8',
    });

    if (result.error || result.status !== 0) {
      continue;
    }

    const files = parseNumstat(result.stdout);

    if (files.length > 0 || source.label === 'working tree diff') {
      return { ...source, files };
    }
  }

  return null;
}

function currentChangedPaths() {
  const sources = currentDiffSources('--name-only');

  for (const source of sources) {
    const result = spawnSync('git', source.args, {
      cwd: rootDir,
      encoding: 'utf8',
    });

    if (result.error || result.status !== 0) {
      continue;
    }

    const paths = result.stdout
      .trim()
      .split('\n')
      .map((path) => path.trim())
      .filter(Boolean);

    if (paths.length > 0 || source.label === 'working tree diff') {
      return { ...source, paths };
    }
  }

  return null;
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
  developerRelative,
  indexFile,
  indexSessions,
  journalSessions,
}) {
  developerRelative ||= dirname((journalSessions || [])[0]?.file || '.');

  const validationFailures = [];
  const sessions = new Map();
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

function parseWorkspaceIndexSessions(file) {
  return parseWorkspaceIndexSessionsFromText(file, readText(file), { onDuplicate: fail });
}

export function parseWorkspaceIndexSessionsFromText(file, text, options = {}) {
  const onDuplicate = options.onDuplicate || (() => {});
  const sessions = new Map();

  for (const match of text.matchAll(/^\|\s*(\d+)\s*\|[^|]*\|[^|]*\|\s*([^|]*?)\s*\|[^|]*\|$/gm)) {
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
  const headingMatch = new RegExp(`^###\\s+${escapeRegExp(heading)}\\s*$`, 'm').exec(markdown);

  if (!headingMatch) {
    return '';
  }

  const rest = markdown.slice((headingMatch.index ?? 0) + headingMatch[0].length);
  const nextHeading = /^###\s+/m.exec(rest);

  return nextHeading ? rest.slice(0, nextHeading.index) : rest;
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

function readJson(file) {
  return JSON.parse(readText(file));
}

function readText(file) {
  return readFileSync(resolve(rootDir, file), 'utf8');
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
