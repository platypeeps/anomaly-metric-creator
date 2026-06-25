"""Inline debug UI shell for server mode."""

from __future__ import annotations


DEBUG_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AMC Debug Console</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7f8;
      --panel: #ffffff;
      --line: #d8dee4;
      --text: #1f2933;
      --muted: #5f6b76;
      --ok: #0f7b43;
      --warn: #a15c00;
      --bad: #b42318;
      --accent: #1b65a7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: var(--bg);
    }
    header {
      position: sticky;
      top: 0;
      z-index: 3;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
    }
    h1, h2 { margin: 0; font-weight: 650; letter-spacing: 0; }
    h1 { font-size: 18px; }
    h2 { font-size: 15px; }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(340px, 0.8fr);
      gap: 12px;
      padding: 12px;
    }
    section {
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--panel);
    }
    section > .bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .stack { display: grid; gap: 12px; }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 12px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      min-height: 62px;
    }
    .mini-charts {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 8px;
      padding: 0 12px 12px;
    }
    .mini-chart {
      display: grid;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
    }
    .bartrack {
      height: 8px;
      overflow: hidden;
      border-radius: 999px;
      background: #edf1f4;
    }
    .barfill {
      height: 100%;
      min-width: 2px;
      border-radius: 999px;
      background: var(--accent);
    }
    .label { color: var(--muted); font-size: 12px; }
    .value { margin-top: 3px; font-size: 18px; font-weight: 650; overflow-wrap: anywhere; }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
    }
    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th { color: var(--muted); font-size: 12px; font-weight: 650; }
    tr:hover td { background: #f9fbfc; }
    .status {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 1px 7px;
      border-radius: 999px;
      border: 1px solid var(--line);
      font-size: 12px;
      font-weight: 650;
      white-space: nowrap;
    }
    .supported { color: var(--ok); border-color: #9dd7b8; background: #eefaf3; }
    .partial { color: var(--warn); border-color: #e8c68e; background: #fff8eb; }
    .unsupported { color: var(--bad); border-color: #efaaa3; background: #fff1f0; }
    tr.selected td { background: #eef5fb; }
    #commands tr, #searchResults tr, #scenarios tr, #profiles tr { cursor: pointer; }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      padding: 0 12px 12px;
    }
    .detail-grid table, .scenario-detail table { font-size: 12px; }
    .detail-grid th, .scenario-detail th { width: 42%; }
    .scenario-shell { padding: 12px; }
    .scenario-detail {
      border-top: 1px solid var(--line);
      padding-top: 12px;
      margin-top: 12px;
    }
    .scenario-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .scenario-head h3 { margin: 0; font-size: 15px; letter-spacing: 0; }
    .scenario-head .muted { font-size: 12px; }
    .scenario-section-title {
      margin: 12px 0 6px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .cmdbar {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .searchbar {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(0, 0.75fr) minmax(0, 0.75fr) auto;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .filterbar {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 8px;
      padding: 12px;
    }
    .actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }
    input, button, select {
      font: inherit;
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 34px;
      padding: 6px 9px;
      background: #fff;
    }
    button {
      color: #fff;
      background: var(--accent);
      border-color: var(--accent);
      cursor: pointer;
      font-weight: 650;
    }
    button.secondary {
      color: var(--accent);
      background: #eef5fb;
      border-color: #b8d2e8;
    }
    button.small {
      min-height: 28px;
      padding: 4px 7px;
      font-size: 12px;
    }
    .freshness {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #f9fbfc;
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .timeline-kind {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
    }
    .resource-row { cursor: pointer; }
    .drawer {
      position: fixed;
      top: 58px;
      right: 12px;
      bottom: 12px;
      z-index: 5;
      display: none;
      width: min(620px, calc(100vw - 24px));
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      box-shadow: 0 18px 44px rgb(15 23 32 / 18%);
    }
    .drawer.open {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }
    .drawer-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
    }
    .drawer pre {
      max-height: none;
      height: 100%;
      border-top: 0;
    }
    pre {
      margin: 0;
      padding: 12px;
      max-height: 520px;
      overflow: auto;
      border-top: 1px solid var(--line);
      background: #0f1720;
      color: #e5edf5;
      font-size: 12px;
      line-height: 1.45;
    }
    .muted { color: var(--muted); }
    @media (max-width: 1000px) {
      main { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .mini-charts, .filterbar { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <h1>AMC Debug Console</h1>
    <div class="actions">
      <span class="freshness" id="runtimeFreshness">runtime live</span>
      <span class="freshness" id="scenarioCatalogFreshness">catalog pending</span>
      <span class="muted" id="clock">--</span>
    </div>
  </header>
  <main>
    <div class="stack">
      <section>
        <div class="bar">
          <h2>State</h2>
          <span id="otel" class="muted"></span>
          <button type="button" id="resetMutations">Reset</button>
        </div>
        <div class="metrics">
          <div class="metric"><div class="label">Scenarios</div><div class="value" id="scenarioCount">0</div></div>
          <div class="metric"><div class="label">Anomalies</div><div class="value" id="anomalyCount">0</div></div>
          <div class="metric"><div class="label">Commands</div><div class="value" id="commandCount">0</div></div>
          <div class="metric"><div class="label">Unsupported</div><div class="value" id="unsupportedCount">0</div></div>
          <div class="metric"><div class="label">Generations</div><div class="value" id="generationCount">0</div></div>
          <div class="metric"><div class="label">Mutations</div><div class="value" id="mutationVersion">0</div></div>
        </div>
        <div class="mini-charts" id="miniCharts"></div>
        <div class="detail-grid">
          <table>
            <thead><tr><th colspan="2">Runtime</th></tr></thead>
            <tbody id="runtimeDetails"></tbody>
          </table>
          <table>
            <thead><tr><th colspan="2">Mutable State</th></tr></thead>
            <tbody id="mutationDetails"></tbody>
          </table>
        </div>
        <table>
          <thead><tr><th>Workload Overlay</th><th style="width:95px">Replicas</th><th style="width:130px">Status</th><th style="width:170px">Updated</th></tr></thead>
          <tbody id="workloadMutations"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Filters</h2><span class="muted">global</span></div>
        <div class="filterbar">
          <select id="globalScenarioFilter"><option value="">any scenario</option></select>
          <select id="globalKindFilter">
            <option value="">any resource</option>
            <option value="pods">pods</option>
            <option value="deployments">deployments</option>
            <option value="services">services</option>
            <option value="hpa">hpa</option>
            <option value="events">events</option>
            <option value="helm">helm</option>
          </select>
          <select id="globalStatusFilter">
            <option value="">any status</option>
            <option value="supported">supported</option>
            <option value="partial">partial</option>
            <option value="unsupported">unsupported</option>
          </select>
          <select id="globalFamilyFilter">
            <option value="">any family</option>
            <option value="kubectl">kubectl</option>
            <option value="helm">helm</option>
            <option value="kubernetes-api">kubernetes-api</option>
          </select>
          <select id="globalWindowFilter">
            <option value="">all time</option>
            <option value="15">15m</option>
            <option value="60">1h</option>
            <option value="360">6h</option>
            <option value="1440">24h</option>
          </select>
        </div>
      </section>
      <section>
        <div class="bar">
          <h2>Analysis</h2>
          <div class="actions">
            <button type="button" class="secondary small" id="exportTraceJson">Trace JSON</button>
            <button type="button" class="secondary small" id="exportUnsupportedJson">Unsupported JSON</button>
            <button type="button" class="secondary small" id="exportUnsupportedCsv">Unsupported CSV</button>
          </div>
        </div>
        <table>
          <thead><tr><th style="width:105px">Type</th><th style="width:170px">When</th><th>Subject</th><th>Detail</th></tr></thead>
          <tbody id="timelineRows"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Resource Diffs</h2><span class="muted">baseline / overlay</span></div>
        <table>
          <thead><tr><th style="width:120px">Kind</th><th>Resource</th><th>Overlay</th><th>Detail</th></tr></thead>
          <tbody id="resourceDiffs"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Command Trace</h2><span class="muted" id="lastRefresh"></span></div>
        <form class="cmdbar" id="commandForm">
          <input id="commandInput" value="kubectl get pods -n saas-prod">
          <button type="submit">Run</button>
        </form>
        <table>
          <thead><tr><th style="width:70px">ID</th><th>Command</th><th style="width:125px">Status</th><th style="width:110px">Exit</th></tr></thead>
          <tbody id="commands"></tbody>
        </table>
        <pre id="details">{}</pre>
      </section>
      <section>
        <div class="bar"><h2>Resources</h2><span class="muted" id="resourceCount">0 resources</span></div>
        <table>
          <thead><tr><th>Name</th><th>Status</th><th>Signal</th><th>Scenario</th></tr></thead>
          <tbody id="resourcesTable"></tbody>
        </table>
      </section>
    </div>
    <div class="stack">
      <section>
        <div class="bar"><h2>Search</h2><span class="muted" id="searchCount">0 matches</span></div>
        <form class="searchbar" id="searchForm">
          <input id="searchInput" placeholder="raw command, output, fingerprint">
          <select id="searchStatus">
            <option value="">any status</option>
            <option value="supported">supported</option>
            <option value="partial">partial</option>
            <option value="unsupported">unsupported</option>
          </select>
          <select id="searchFamily">
            <option value="">any family</option>
            <option value="kubectl">kubectl</option>
            <option value="helm">helm</option>
            <option value="kubernetes-api">kubernetes-api</option>
          </select>
          <button type="submit">Search</button>
        </form>
        <table>
          <thead><tr><th>Command</th><th style="width:112px">Status</th></tr></thead>
          <tbody id="searchResults"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Unsupported Explorer</h2><span class="muted">grouped</span></div>
        <table>
          <thead><tr><th>Fingerprint</th><th style="width:70px">Count</th><th style="width:92px">Test</th></tr></thead>
          <tbody id="unsupported"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Active Profiles</h2><span class="muted">scenario overlays</span></div>
        <table>
          <thead><tr><th>Scenario</th><th>Summary</th></tr></thead>
          <tbody id="profiles"></tbody>
        </table>
      </section>
      <section>
        <div class="bar"><h2>Scenario Catalog</h2><span class="muted" id="scenarioCatalogCount">0 scenarios</span></div>
        <div class="scenario-shell">
          <table>
            <thead><tr><th>Scenario</th><th style="width:88px">Severity</th><th style="width:110px">Days</th></tr></thead>
            <tbody id="scenarios"></tbody>
          </table>
          <div class="scenario-detail" id="scenarioDetail"></div>
        </div>
      </section>
      <section>
        <div class="bar"><h2>Recent Events</h2><span class="muted">cluster</span></div>
        <table>
          <thead><tr><th>Reason</th><th>Namespace</th><th>Object</th><th>Message</th></tr></thead>
          <tbody id="events"></tbody>
        </table>
      </section>
    </div>
  </main>
  <aside class="drawer" id="resourceDrawer">
    <div class="drawer-head">
      <h2 id="resourceDrawerTitle">Resource</h2>
      <button type="button" class="secondary small" id="closeResourceDrawer">Close</button>
    </div>
    <pre id="resourceDrawerBody">{}</pre>
  </aside>
  <script>
    const $ = (id) => document.getElementById(id);
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[ch]));
    const AUTH_STORAGE_KEY = "amc.debug.authToken";
    let authPromptDismissed = false;
    let scenarioCatalog = {active: [], known: []};
    let scenarioCatalogPromise = null;
    let scenarioCatalogRendered = false;
    let selectedScenarioId = "";
    let latestState = null;
    let latestCommands = [];
    let latestUnsupported = {items: []};
    let latestResources = {};
    const RESOURCE_KINDS = ["pods", "deployments", "services", "hpa", "events", "pvc", "statefulsets", "ingress", "configmaps", "secrets", "jobs", "cronjobs", "serviceaccounts", "nodes"];
    function bootstrapAuthToken() {
      const params = new URLSearchParams(window.location.search);
      const token = params.get("token") || params.get("auth_token");
      if (!token) return;
      window.localStorage.setItem(AUTH_STORAGE_KEY, token);
      params.delete("token");
      params.delete("auth_token");
      const query = params.toString();
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
      );
    }
    async function request(url, options = {}, retryAuth = true) {
      const token = window.localStorage.getItem(AUTH_STORAGE_KEY);
      const res = await fetch(url, {
        ...options,
        headers: token
          ? {...(options.headers || {}), authorization: `Bearer ${token}`}
          : (options.headers || {})
      });
      if (res.status === 401 && retryAuth) {
        const currentToken = window.localStorage.getItem(AUTH_STORAGE_KEY);
        if (currentToken && currentToken !== token) return await request(url, options, false);
        if (authPromptDismissed) throw new Error(`${res.status} ${res.statusText}`);
        const token = window.prompt("Bearer token");
        if (token) {
          window.localStorage.setItem(AUTH_STORAGE_KEY, token);
          return await request(url, options, false);
        }
        authPromptDismissed = true;
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res;
    }
    async function getJSON(url) {
      const res = await request(url);
      return await res.json();
    }
    async function getScenarioCatalog() {
      if (!scenarioCatalogPromise) {
        scenarioCatalogPromise = getJSON("/v1/scenarios")
          .then((payload) => {
            scenarioCatalog = payload;
            return payload;
          })
          .catch((error) => {
            scenarioCatalogPromise = null;
            throw error;
          });
      }
      return await scenarioCatalogPromise;
    }
    async function postJSON(url, body) {
      const res = await request(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(body)
      });
      return await res.json();
    }
    function downloadBlob(filename, content, type) {
      const blob = new Blob([content], {type});
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    }
    function downloadJSON(filename, payload) {
      downloadBlob(filename, `${JSON.stringify(payload, null, 2)}\n`, "application/json");
    }
    function csvCell(value) {
      const text = String(value ?? "");
      return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
    }
    function downloadCSV(filename, rows, columns) {
      const header = columns.map((column) => csvCell(column.label)).join(",");
      const body = rows.map((row) => columns.map((column) => csvCell(column.value(row))).join(",")).join("\n");
      downloadBlob(filename, `${header}\n${body}${body ? "\n" : ""}`, "text/csv");
    }
    function statusClass(status) {
      return status === "supported" ? "supported" : status === "partial" ? "partial" : "unsupported";
    }
    function severityClass(severity) {
      return severity === "high" ? "unsupported" : severity === "medium" ? "partial" : "supported";
    }
    function valueOrDash(value) {
      return value === undefined || value === null || value === "" ? "-" : value;
    }
    function renderKeyValues(targetId, rows) {
      $(targetId).innerHTML = rows.map(([key, value]) => `
        <tr><th>${esc(key)}</th><td>${esc(valueOrDash(value))}</td></tr>
      `).join("");
    }
    function formatSeconds(seconds) {
      if (seconds === undefined || seconds === null || seconds === "") return "-";
      const value = Number(seconds);
      if (!Number.isFinite(value)) return String(seconds);
      if (value >= 3600) return `${Math.round((value / 3600) * 10) / 10}h`;
      if (value >= 60) return `${Math.round((value / 60) * 10) / 10}m`;
      return `${value}s`;
    }
    function formatOffset(seconds) {
      return formatSeconds(seconds);
    }
    function activeFilters() {
      return {
        scenario: $("globalScenarioFilter")?.value || "",
        kind: $("globalKindFilter")?.value || "",
        status: $("globalStatusFilter")?.value || "",
        family: $("globalFamilyFilter")?.value || "",
        windowMinutes: Number($("globalWindowFilter")?.value || 0),
      };
    }
    function parseMaybeTime(value) {
      if (!value) return null;
      const direct = Date.parse(value);
      if (Number.isFinite(direct)) return direct;
      const normalized = Date.parse(String(value).replace(" ", "T") + "Z");
      return Number.isFinite(normalized) ? normalized : null;
    }
    function withinWindow(value, filters) {
      if (!filters.windowMinutes) return true;
      const parsed = parseMaybeTime(value);
      if (parsed === null) return true;
      return Date.now() - parsed <= filters.windowMinutes * 60 * 1000;
    }
    function itemScenarios(item) {
      return item.active_scenarios || item.scenario_ids || item.scenarios || [];
    }
    function matchesScenario(item, filters) {
      if (!filters.scenario) return true;
      return itemScenarios(item).includes(filters.scenario);
    }
    function matchesCommandFilters(item, filters) {
      if (!matchesScenario(item, filters)) return false;
      if (filters.status && item.support_status !== filters.status) return false;
      if (filters.family && item.command_family !== filters.family) return false;
      return withinWindow(item.received_at_wall_time || item.simulated_time, filters);
    }
    function matchesUnsupportedFilters(item, filters) {
      const examples = item.examples || [];
      if (filters.status && !(item.support_statuses || {})[filters.status]) return false;
      if (filters.family) {
        const fingerprint = String(item.fingerprint || "");
        if (!fingerprint.startsWith(filters.family)
            && !examples.some((example) => String(example.raw_input || "").startsWith(filters.family))) {
          return false;
        }
      }
      if (filters.scenario && !examples.some((example) => matchesScenario({scenario_ids: example.scenario_ids || []}, filters))) {
        return false;
      }
      return withinWindow(item.last_seen || item.first_seen, filters);
    }
    function matchesResourceFilters(kind, row, filters) {
      if (filters.kind && filters.kind !== kind && !(filters.kind === "helm" && kind === "helm")) return false;
      if (!matchesScenario({scenario_ids: row.scenario_ids || []}, filters)) return false;
      if (filters.status) {
        const status = String(row.status || row.deployment_status || row.type || "").toLowerCase();
        if (!status.includes(filters.status)) return false;
      }
      return withinWindow(row.last_seen || row.updated || row.updated_at, filters);
    }
    function renderScenarioFilter(payload) {
      const selected = $("globalScenarioFilter").value;
      const known = payload.known || [];
      $("globalScenarioFilter").innerHTML = `<option value="">any scenario</option>` + known.map((item) => `
        <option value="${esc(item.id)}">${esc(item.id)}</option>
      `).join("");
      $("globalScenarioFilter").value = known.some((item) => item.id === selected) ? selected : "";
    }
    function refreshFilteredViews() {
      if (latestState) renderState(latestState);
      renderCommands(latestCommands);
      renderUnsupported(latestUnsupported);
      renderResources(latestResources);
      renderTimeline(buildTimelineRows(latestState, latestCommands, latestResources));
      renderResourceDiffs(latestState, latestResources);
    }
    function selectScenario(id) {
      selectedScenarioId = id;
      renderScenarioCatalog(scenarioCatalog);
    }
    function renderCommands(items) {
      const filters = activeFilters();
      const visible = items.filter((item) => matchesCommandFilters(item, filters));
      $("commands").innerHTML = visible.map((item) => `
        <tr data-id="${item.id}">
          <td>${item.id}</td>
          <td><code>${esc(item.raw_input)}</code><div class="muted">${esc(item.matched_rule_id)}</div></td>
          <td><span class="status ${statusClass(item.support_status)}">${esc(item.support_status)}</span></td>
          <td>${item.exit_code}</td>
        </tr>`).join("");
      document.querySelectorAll("#commands tr").forEach((row) => {
        row.addEventListener("click", async () => {
          const item = await getJSON(`/v1/debug/commands/${row.dataset.id}`);
          $("details").textContent = JSON.stringify(item, null, 2);
        });
      });
    }
    function renderState(state) {
      $("clock").textContent = `${state.clock.simulated_time} @ ${state.clock.speedup}x`;
      $("runtimeFreshness").textContent = `runtime live ${new Date().toLocaleTimeString()}`;
      const otelText = state.otel.enabled ? `OTEL ${state.otel.thread}` : "OTEL off";
      const generationText = state.generation.enabled
        ? `generation ${state.generation.thread}`
        : "generation off";
      $("otel").textContent = `${otelText} - ${generationText}`;
      $("scenarioCount").textContent = state.active_scenarios.length;
      $("anomalyCount").textContent = state.anomaly_count;
      $("commandCount").textContent = state.command_trace_count;
      $("unsupportedCount").textContent = state.unsupported_group_count;
      $("generationCount").textContent = state.generation.generation_count;
      $("mutationVersion").textContent = state.mutations.version;
      renderMiniCharts(state);
      const release = state.mutations.release || {};
      renderKeyValues("runtimeDetails", [
        ["Generation", `${state.generation.enabled ? "on" : "off"} / ${state.generation.thread}`],
        ["Interval", state.generation.enabled ? `${state.generation.interval_seconds}s` : "-"],
        ["Last seed", state.generation.last_seed],
        ["Last generated", state.generation.last_completed_at],
        ["Generation error", state.generation.last_error],
        ["OTEL", state.otel.enabled ? state.otel.thread : "off"],
        ["OTEL batches", state.otel.stream_batches],
        ["Last OTEL", state.otel.last_completed_at],
      ]);
      const drift = state.mutations.drift || {};
      renderKeyValues("mutationDetails", [
        ["Version", state.mutations.version],
        ["Workloads", Object.keys(state.mutations.workloads || {}).length],
        ["Deleted pods", (state.mutations.deleted_pods || []).join(", ")],
        ["Created resources", Object.values(state.mutations.created_resources || {}).reduce((total, items) => total + items.length, 0)],
        ["Deleted resources", Object.values(state.mutations.deleted_resources || {}).reduce((total, items) => total + items.length, 0)],
        ["Extra events", state.mutations.extra_event_count],
        ["Drift namespaces", (drift.namespaces || []).join(", ")],
        ["Overlay drift", `workloads ${drift.workloads || 0}, created ${drift.created_resources || 0}, deleted ${drift.deleted_resources || 0}, events ${drift.event_overlays || 0}`],
        ["Release", release.uninstalled ? "uninstalled" : `${release.revision_count || 0} revisions`],
        ["Release updated", release.updated_at],
      ]);
      renderWorkloadMutations(state.mutations.workloads || {});
      $("profiles").innerHTML = state.profiles.map((item) => `
        <tr data-id="${esc(item.scenario_id)}"><td>${esc(item.scenario_id)}</td><td>${esc(item.summary)}</td></tr>
      `).join("");
      document.querySelectorAll("#profiles tr").forEach((row) => {
        row.addEventListener("click", () => selectScenario(row.dataset.id));
      });
    }
    function renderWorkloadMutations(workloads) {
      const entries = Object.entries(workloads).sort(([left], [right]) => left.localeCompare(right));
      $("workloadMutations").innerHTML = entries.length ? entries.map(([name, mutation]) => `
        <tr>
          <td>${esc(name)}</td>
          <td>${esc(valueOrDash(mutation.replicas))}</td>
          <td>${esc(valueOrDash(mutation.deployment_status || mutation.pod_status))}</td>
          <td>${esc(valueOrDash(mutation.updated_at))}</td>
        </tr>
      `).join("") : `<tr><td colspan="4" class="muted">none</td></tr>`;
    }
    function renderMiniCharts(state) {
      const values = [
        ["Generations", state.generation.generation_count || 0],
        ["Anomalies", state.anomaly_count || 0],
        ["OTEL batches", state.otel.stream_batches || 0],
        ["Commands", state.command_trace_count || 0],
      ];
      const max = Math.max(1, ...values.map(([, value]) => Number(value) || 0));
      $("miniCharts").innerHTML = values.map(([label, value]) => {
        const width = Math.max(2, Math.round(((Number(value) || 0) / max) * 100));
        return `
          <div class="mini-chart">
            <div class="label">${esc(label)}</div>
            <div class="bartrack"><div class="barfill" style="width:${width}%"></div></div>
            <div class="value">${esc(value)}</div>
          </div>
        `;
      }).join("");
    }
    function renderResources(resources) {
      const filters = activeFilters();
      const selectedKind = filters.kind || "pods";
      const sourceRows = selectedKind === "helm"
        ? [{
            name: "simulated-saas",
            status: latestState?.mutations?.release?.uninstalled ? "uninstalled" : "deployed",
            revision: latestState?.mutations?.release?.revision_count || 0,
            scenario_ids: latestState?.active_scenarios || [],
          }]
        : (resources[selectedKind] || []);
      const rows = sourceRows.filter((row) => matchesResourceFilters(selectedKind, row, {...filters, kind: selectedKind}));
      $("resourceCount").textContent = `${rows.length} ${selectedKind}`;
      $("resourcesTable").innerHTML = rows.length ? rows.map((row) => `
        <tr class="resource-row" data-kind="${esc(selectedKind)}" data-name="${esc(row.name || row.object || row.reason || "")}" data-namespace="${esc(row.namespace || "")}">
          <td>${esc(row.name || row.object || row.reason)}</td>
          <td>${esc(row.status || row.deployment_status || row.type || "-")}</td>
          <td>${esc(row.restarts ?? row.ready ?? row.count ?? row.revision ?? "-")}</td>
          <td>${esc((row.scenario_ids || []).join(", "))}</td>
        </tr>`).join("") : `<tr><td colspan="4" class="muted">none</td></tr>`;
      document.querySelectorAll("#resourcesTable tr.resource-row").forEach((row) => {
        row.addEventListener("click", () => openResourceDrawer(row.dataset.kind, row.dataset.name, row.dataset.namespace));
      });
      $("events").innerHTML = resources.events.map((event) => `
        <tr><td>${esc(event.reason)}</td><td>${esc(event.namespace || "-")}</td><td>${esc(event.object)}</td><td>${esc(event.message)}</td></tr>
      `).join("");
    }
    function renderUnsupported(payload) {
      const filters = activeFilters();
      const items = (payload.items || []).filter((item) => matchesUnsupportedFilters(item, filters));
      $("unsupported").innerHTML = items.map((item, index) => `
        <tr>
          <td>${esc(item.fingerprint)}<div class="muted">${esc(item.guessed_intent)}</div></td>
          <td>${item.count}</td>
          <td><button type="button" class="secondary small" data-promote="${index}">Copy</button></td>
        </tr>`).join("");
      document.querySelectorAll("#unsupported button[data-promote]").forEach((button) => {
        button.addEventListener("click", async () => {
          const item = items[Number(button.dataset.promote)];
          const snippet = pytestSnippetForUnsupported(item);
          if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(snippet);
          }
          $("details").textContent = snippet;
        });
      });
    }
    function renderSearch(payload) {
      const filters = activeFilters();
      const items = (payload.items || []).filter((item) => matchesCommandFilters(item, filters));
      const total = filters.windowMinutes ? items.length : payload.total;
      const backend = payload.search_backend ? ` via ${payload.search_backend}` : "";
      $("searchCount").textContent = `${total} match${total === 1 ? "" : "es"}${backend}`;
      $("searchResults").innerHTML = items.map((item) => `
        <tr data-id="${item.id}">
          <td><code>${esc(item.raw_input)}</code><div class="muted">${esc(item.fingerprint)}</div></td>
          <td><span class="status ${statusClass(item.support_status)}">${esc(item.support_status)}</span></td>
        </tr>`).join("");
      document.querySelectorAll("#searchResults tr").forEach((row) => {
        row.addEventListener("click", async () => {
          const item = await getJSON(`/v1/debug/commands/${row.dataset.id}`);
          $("details").textContent = JSON.stringify(item, null, 2);
        });
      });
    }
    function buildTimelineRows(state, commands, resources) {
      const rows = [];
      const filters = activeFilters();
      if (state) {
        rows.push({
          type: "runtime",
          when: state.clock?.simulated_time || "",
          subject: "state refresh",
          detail: `${state.command_trace_count || 0} commands, ${state.anomaly_count || 0} anomalies`,
        });
        if (state.generation?.generation_count) {
          rows.push({
            type: "generation",
            when: state.generation.last_completed_at || state.clock?.simulated_time || "",
            subject: `${state.generation.generation_count} generation passes`,
            detail: `thread ${state.generation.thread}, seed ${valueOrDash(state.generation.last_seed)}`,
          });
        }
        if (state.otel?.stream_batches) {
          rows.push({
            type: "otel",
            when: state.otel.last_completed_at || state.clock?.simulated_time || "",
            subject: `${state.otel.stream_batches} OTEL batches`,
            detail: state.otel.thread || "streamed",
          });
        }
      }
      (commands || []).filter((item) => matchesCommandFilters(item, filters)).forEach((item) => {
        rows.push({
          type: item.command_family || "command",
          when: item.received_at_wall_time || item.simulated_time || "",
          subject: item.raw_input,
          detail: `${item.support_status} / ${item.matched_rule_id}`,
        });
      });
      ((resources || {}).events || [])
        .filter((event) => matchesResourceFilters("events", event, {...filters, kind: "events"}))
        .forEach((event) => {
          rows.push({
            type: "event",
            when: event.last_seen || "",
            subject: event.object || event.reason,
            detail: `${event.reason}: ${event.message}`,
          });
        });
      rows.sort((left, right) => (parseMaybeTime(right.when) || 0) - (parseMaybeTime(left.when) || 0));
      return rows.slice(0, 80);
    }
    function renderTimeline(rows) {
      $("timelineRows").innerHTML = rows.length ? rows.map((row) => `
        <tr>
          <td><span class="timeline-kind">${esc(row.type)}</span></td>
          <td>${esc(valueOrDash(row.when))}</td>
          <td>${esc(row.subject)}</td>
          <td>${esc(row.detail)}</td>
        </tr>
      `).join("") : `<tr><td colspan="4" class="muted">none</td></tr>`;
    }
    function renderResourceDiffs(state, resources) {
      const mutations = state?.mutations || {};
      const release = mutations.release || {};
      const rows = [];
      Object.entries(mutations.workloads || {}).forEach(([name, mutation]) => {
        rows.push(["deployment", name, "workload overlay", `replicas ${valueOrDash(mutation.replicas)}, status ${valueOrDash(mutation.deployment_status || mutation.pod_status)}`]);
      });
      (mutations.deleted_pods || []).forEach((name) => {
        rows.push(["pod", name, "deleted", "filtered from pod snapshots"]);
      });
      Object.entries(mutations.created_resources || {}).forEach(([kind, names]) => {
        (names || []).forEach((name) => rows.push([kind, name, "created", "merged into resource snapshots"]));
      });
      Object.entries(mutations.deleted_resources || {}).forEach(([kind, names]) => {
        (names || []).forEach((name) => rows.push([kind, name, "deleted", "filtered from resource snapshots"]));
      });
      if (release.uninstalled || release.revision_count || Object.keys(release.values || {}).length) {
        rows.push(["helm", "simulated-saas", release.uninstalled ? "uninstalled" : "release overlay", `${release.revision_count || 0} revisions, ${Object.keys(release.values || {}).length} values`]);
      }
      const replacementPods = ((resources || {}).pods || []).filter((pod) => String(pod.name || "").includes("-recreated-"));
      replacementPods.forEach((pod) => rows.push(["pod", pod.name, "replacement", `controller reconciled ${pod.component || "workload"}`]));
      $("resourceDiffs").innerHTML = rows.length ? rows.map((row) => `
        <tr><td>${esc(row[0])}</td><td>${esc(row[1])}</td><td>${esc(row[2])}</td><td>${esc(row[3])}</td></tr>
      `).join("") : `<tr><td colspan="4" class="muted">none</td></tr>`;
    }
    function findResourceSnapshot(kind, name, namespace) {
      const rows = latestResources[kind] || [];
      return rows.find((row) => String(row.name || row.object || row.reason || "") === name
        && (!namespace || !row.namespace || row.namespace === namespace));
    }
    function resourceApiPath(kind, name, namespace) {
      const ns = namespace || latestState?.namespace || "saas-prod";
      const encodedName = encodeURIComponent(name);
      const encodedNs = encodeURIComponent(ns);
      const core = {
        pods: "pods",
        services: "services",
        endpoints: "endpoints",
        configmaps: "configmaps",
        secrets: "secrets",
        serviceaccounts: "serviceaccounts",
        pvc: "persistentvolumeclaims",
      };
      if (core[kind]) return `/api/v1/namespaces/${encodedNs}/${core[kind]}/${encodedName}`;
      if (kind === "nodes") return `/api/v1/nodes/${encodedName}`;
      if (["deployments", "replicasets", "daemonsets", "statefulsets"].includes(kind)) {
        return `/apis/apps/v1/namespaces/${encodedNs}/${kind}/${encodedName}`;
      }
      if (kind === "hpa") return `/apis/autoscaling/v2/namespaces/${encodedNs}/horizontalpodautoscalers/${encodedName}`;
      if (["jobs", "cronjobs"].includes(kind)) return `/apis/batch/v1/namespaces/${encodedNs}/${kind}/${encodedName}`;
      if (kind === "ingress") return `/apis/networking.k8s.io/v1/namespaces/${encodedNs}/ingresses/${encodedName}`;
      if (kind === "endpointslices") return `/apis/discovery.k8s.io/v1/namespaces/${encodedNs}/endpointslices/${encodedName}`;
      return "";
    }
    async function openResourceDrawer(kind, name, namespace) {
      $("resourceDrawer").classList.add("open");
      $("resourceDrawerTitle").textContent = `${kind}/${name}`;
      $("resourceDrawerBody").textContent = "loading";
      if (kind === "helm") {
        $("resourceDrawerBody").textContent = JSON.stringify(latestState?.mutations?.release || {}, null, 2);
        return;
      }
      const path = resourceApiPath(kind, name, namespace);
      try {
        const payload = path ? await getJSON(path) : findResourceSnapshot(kind, name, namespace);
        $("resourceDrawerBody").textContent = JSON.stringify(payload || {}, null, 2);
      } catch (error) {
        const snapshot = findResourceSnapshot(kind, name, namespace);
        $("resourceDrawerBody").textContent = JSON.stringify({error: String(error), snapshot}, null, 2);
      }
    }
    function pytestSnippetForUnsupported(item) {
      const example = (item.examples || [])[0] || {};
      const rawInput = example.raw_input || "kubectl get pods -n saas-prod";
      const name = String(item.fingerprint || "unsupported_command")
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 70) || "unsupported_command";
      return [
        `def test_${name}_is_supported(amc, tmp_path):`,
        `    state = _build_state(amc, tmp_path, scenarios="cache_leak_restart", days=3)`,
        `    result = server.run_command(state, command=${JSON.stringify(rawInput)})`,
        `    assert result["result"]["support_status"] == "supported"`,
        `    assert result["result"]["matched_rule_id"]`,
        "",
      ].join("\n");
    }
    function renderScenarioCatalog(payload) {
      scenarioCatalog = payload;
      renderScenarioFilter(payload);
      const known = payload.known || [];
      const active = new Set(payload.active || []);
      $("scenarioCatalogCount").textContent = `${known.length} scenario${known.length === 1 ? "" : "s"}`;
      $("scenarioCatalogFreshness").textContent = `catalog cached ${known.length}`;
      if (!selectedScenarioId || !known.some((item) => item.id === selectedScenarioId)) {
        selectedScenarioId = known.find((item) => active.has(item.id))?.id || known[0]?.id || "";
      }
      $("scenarios").innerHTML = known.map((item) => `
        <tr data-id="${esc(item.id)}" class="${item.id === selectedScenarioId ? "selected" : ""}">
          <td>
            <strong>${esc(item.id)}</strong>
            <div class="muted">${esc(item.name)}</div>
            <div class="muted">${esc((item.components_touched || []).join(", "))}</div>
          </td>
          <td>
            <span class="status ${severityClass(item.severity)}">${esc(item.severity)}</span>
            ${active.has(item.id) ? `<div class="muted">active</div>` : ""}
          </td>
          <td>${esc(item.days_required)}</td>
        </tr>
      `).join("");
      document.querySelectorAll("#scenarios tr").forEach((row) => {
        row.addEventListener("click", () => selectScenario(row.dataset.id));
      });
      renderScenarioDetail(known.find((item) => item.id === selectedScenarioId));
    }
    function renderScenarioCatalogOnce(payload) {
      if (scenarioCatalogRendered) return;
      renderScenarioCatalog(payload);
      scenarioCatalogRendered = true;
    }
    function renderSpecRows(specs) {
      if (!specs.length) return `<tr><td colspan="4" class="muted">none</td></tr>`;
      return specs.map((spec) => `
        <tr>
          <td>${esc(spec.component)}<div class="muted">${esc(spec.metric)}</div></td>
          <td>${esc(spec.description)}</td>
          <td>${esc(formatOffset(spec.time_offset_seconds))}</td>
          <td>${esc(formatSeconds(spec.duration_seconds))}</td>
        </tr>
      `).join("");
    }
    function renderStringRows(items) {
      if (!items.length) return `<tr><td class="muted">none</td></tr>`;
      return items.map((item) => `<tr><td>${esc(item)}</td></tr>`).join("");
    }
    function renderImpactRows(impacts) {
      if (!impacts.length) return `<tr><td colspan="4" class="muted">none</td></tr>`;
      return impacts.map((impact) => `
        <tr>
          <td>${esc(impact.component)}</td>
          <td>${esc(impact.deployment_status)}</td>
          <td>${esc(impact.pod_status)}</td>
          <td>${esc(valueOrDash(impact.ready || impact.ready_replicas || impact.ready_replicas_delta))}</td>
        </tr>
      `).join("");
    }
    function renderScenarioDetail(item) {
      if (!item) {
        $("scenarioDetail").innerHTML = `<div class="muted">none</div>`;
        return;
      }
      const profile = item.ops_profile_detail || {};
      $("scenarioDetail").innerHTML = `
        <div class="scenario-head">
          <div>
            <h3>${esc(item.name)}</h3>
            <div class="muted">${esc(item.id)} - ${esc(item.category)} - ${esc((item.components_touched || []).join(", "))}</div>
          </div>
          <span class="status ${severityClass(item.severity)}">${esc(item.severity)}</span>
        </div>
        <div class="detail-grid">
          <table>
            <tbody>
              <tr><th>Days required</th><td>${esc(item.days_required)}</td></tr>
              <tr><th>Primary signals</th><td>${esc((item.primary_specs || []).length)}</td></tr>
              <tr><th>Cascade signals</th><td>${esc((item.cascade_specs || []).length)}</td></tr>
              <tr><th>Ops profile</th><td>${profile.summary ? "yes" : "no"}</td></tr>
            </tbody>
          </table>
          <table>
            <tbody>
              <tr><th>Operator summary</th><td>${esc(valueOrDash(profile.summary))}</td></tr>
              <tr><th>Affected</th><td>${esc((profile.affected_components || []).join(", "))}</td></tr>
              <tr><th>Rollout note</th><td>${esc(valueOrDash(profile.rollout_note))}</td></tr>
              <tr><th>Helm notes</th><td>${esc(valueOrDash(profile.helm_notes))}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="scenario-section-title">Primary Signals</div>
        <table>
          <thead><tr><th>Component / Metric</th><th>Description</th><th style="width:80px">Offset</th><th style="width:90px">Duration</th></tr></thead>
          <tbody>${renderSpecRows(item.primary_specs || [])}</tbody>
        </table>
        <div class="scenario-section-title">Cascade Signals</div>
        <table>
          <thead><tr><th>Component / Metric</th><th>Description</th><th style="width:80px">Offset</th><th style="width:90px">Duration</th></tr></thead>
          <tbody>${renderSpecRows(item.cascade_specs || [])}</tbody>
        </table>
        <div class="scenario-section-title">Ops Events</div>
        <table><tbody>${renderStringRows(profile.events || [])}</tbody></table>
        <div class="scenario-section-title">Ops Logs</div>
        <table><tbody>${renderStringRows(profile.logs || [])}</tbody></table>
        <div class="scenario-section-title">Kubernetes Impacts</div>
        <table>
          <thead><tr><th>Component</th><th>Status</th><th>Pod</th><th>Ready</th></tr></thead>
          <tbody>${renderImpactRows(profile.impacts || [])}</tbody>
        </table>
      `;
    }
    async function runSearch() {
      const params = new URLSearchParams();
      const filters = activeFilters();
      if ($("searchInput").value) params.set("q", $("searchInput").value);
      if ($("searchStatus").value || filters.status) params.set("status", $("searchStatus").value || filters.status);
      if ($("searchFamily").value || filters.family) params.set("family", $("searchFamily").value || filters.family);
      if (filters.scenario) params.set("scenario", filters.scenario);
      params.set("limit", "25");
      const payload = await getJSON(`/v1/debug/search?${params.toString()}`);
      renderSearch(payload);
    }
    async function refresh() {
      try {
        const [state, commands, unsupported, resources, scenarios] = await Promise.all([
          getJSON("/v1/state"),
          getJSON("/v1/debug/commands?limit=80"),
          getJSON("/v1/debug/unsupported"),
          getJSON("/v1/debug/resources"),
          getScenarioCatalog()
        ]);
        latestState = state;
        latestCommands = commands.items || [];
        latestUnsupported = unsupported;
        latestResources = resources;
        renderState(state);
        renderCommands(latestCommands);
        renderUnsupported(unsupported);
        renderResources(resources);
        renderTimeline(buildTimelineRows(state, latestCommands, resources));
        renderResourceDiffs(state, resources);
        renderScenarioCatalogOnce(scenarios);
        await runSearch();
        $("lastRefresh").textContent = new Date().toLocaleTimeString();
      } catch (error) {
        $("details").textContent = String(error);
      }
    }
    $("commandForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = await postJSON("/v1/commands", {command: $("commandInput").value});
      $("details").textContent = JSON.stringify(result, null, 2);
      await refresh();
    });
    $("searchForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      await runSearch();
    });
    $("resetMutations").addEventListener("click", async () => {
      const result = await postJSON("/v1/mutations/reset", {});
      $("details").textContent = JSON.stringify(result, null, 2);
      await refresh();
    });
    $("closeResourceDrawer").addEventListener("click", () => {
      $("resourceDrawer").classList.remove("open");
    });
    ["globalScenarioFilter", "globalKindFilter", "globalStatusFilter", "globalFamilyFilter", "globalWindowFilter"].forEach((id) => {
      $(id).addEventListener("change", async () => {
        refreshFilteredViews();
        await runSearch();
      });
    });
    $("exportTraceJson").addEventListener("click", async () => {
      const payload = await getJSON("/v1/debug/commands/export");
      downloadJSON("amc-command-traces.json", payload);
    });
    $("exportUnsupportedJson").addEventListener("click", () => {
      const filters = activeFilters();
      const items = (latestUnsupported.items || []).filter((item) => matchesUnsupportedFilters(item, filters));
      downloadJSON("amc-unsupported-backlog.json", {items});
    });
    $("exportUnsupportedCsv").addEventListener("click", () => {
      const filters = activeFilters();
      const items = (latestUnsupported.items || []).filter((item) => matchesUnsupportedFilters(item, filters));
      downloadCSV("amc-unsupported-backlog.csv", items, [
        {label: "fingerprint", value: (row) => row.fingerprint},
        {label: "count", value: (row) => row.count},
        {label: "first_seen", value: (row) => row.first_seen},
        {label: "last_seen", value: (row) => row.last_seen},
        {label: "guessed_intent", value: (row) => row.guessed_intent},
        {label: "example", value: (row) => (row.examples || [])[0]?.raw_input || ""},
      ]);
    });
    bootstrapAuthToken();
    refresh();
    setInterval(refresh, 1500);
  </script>
</body>
</html>
"""
