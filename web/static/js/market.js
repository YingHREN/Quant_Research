import { getMacroHistory, getMarketOverview } from "./api.js";
import { createMacroHistoryCharts } from "./macro-history-chart.mjs";
import {
  applyDocumentLocale,
  getLocale,
  setLocale,
  subscribeLocale,
  t,
  translateError,
} from "./i18n.js";

const state = {
  horizon: 5,
  sector: "semiconductor",
  payload: null,
  requestId: 0,
  status: { kind: "idle", error: null },
  macroHistory: {
    range: "3y",
    benchmark: "SPY",
    payload: null,
    requestId: 0,
    status: { kind: "idle", error: null },
  },
};

let macroHistoryCharts = null;

function text(node, value) {
  node.textContent = value == null ? "—" : String(value);
  return node;
}

function element(tag, className = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

function formatScore(value) {
  return value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toFixed(1);
}

function riskDisplayScore(risk = {}) {
  return Number.isFinite(Number(risk.state_score))
    ? risk.state_score
    : risk.score;
}

function riskDetail(risk = {}) {
  if (riskDisplayScore(risk) == null) {
    return unavailableText(risk.unavailable_reason);
  }
  const stateLabel = localized(
    `market.riskState.${risk.state || "unavailable"}`,
    risk.state || "—",
  );
  return `${t("market.risk.detail", {
    state: stateLabel,
    raw: formatScore(risk.raw_score ?? risk.score),
    age: risk.memory_age_sessions ?? "—",
  })} · ${t("market.risk.modelSource")}`;
}

function riskCellText(risk = {}) {
  const score = formatScore(riskDisplayScore(risk));
  if (score === "—") return score;
  const stateLabel = localized(
    `market.riskState.${risk.state || "unavailable"}`,
    risk.state || "—",
  );
  return `${score} · ${stateLabel}`;
}

function formatPercent(value, digits = 1) {
  return value == null || !Number.isFinite(Number(value))
    ? "—"
    : `${(Number(value) * 100).toFixed(digits)}%`;
}

function localized(key, fallback = "") {
  const value = t(key);
  return value === key ? fallback || key : value;
}

function scoreBlock(label, value, detail) {
  const block = element("div", "market-score-block");
  const score = element("strong", "market-score-value");
  text(score, value);
  const explanation = element("span", "market-unavailable-reason");
  text(explanation, detail);
  block.append(
    text(element("span", "market-score-label"), label),
    score,
    explanation,
  );
  return block;
}

function unavailableText(reason) {
  return reason
    ? localized(`market.unavailable.${reason}`, reason)
    : t("market.available");
}

const REFERENCE_FACTOR_KEYS = Object.freeze([
  "qqq_above_ema20",
  "qqq_above_sma50",
  "breadth_above_ema20",
  "breadth_above_sma50",
  "distribution_count_20_safe",
  "atr20_ratio_safe",
]);

function renderReferenceFactors(evidence = []) {
  const root = document.querySelector("#market-reference-factors");
  if (!root) return;
  const byKey = new Map(evidence.map((row) => [row.key, row]));
  const cards = [];
  for (const key of REFERENCE_FACTOR_KEYS) {
    const row = byKey.get(key);
    if (!row) continue;
    const card = element("article", "market-reference-factor");
    card.dataset.state = row.state || "unavailable";
    const heading = element("div", "market-reference-factor-heading");
    heading.append(
      text(element("strong"), localized(`market.evidence.${key}`, key)),
      text(
        element("span", "market-reference-factor-state"),
        localized(`market.state.${row.state}`, row.state),
      ),
      helpMarker(key),
    );
    const values = element("p", "market-reference-factor-values");
    text(
      values,
      `${t("market.value")} ${row.value ?? "—"} · `
      + `${t("market.threshold")} ${row.threshold ?? "—"} · `
      + `${row.window || "—"}`,
    );
    const missing = element("small", "market-unavailable-reason");
    text(
      missing,
      row.unavailable_reason ? unavailableText(row.unavailable_reason) : "",
    );
    card.append(heading, values, missing);
    cards.push(card);
  }
  if (!cards.length) {
    root.replaceChildren(
      text(element("p", "market-empty"), t("market.evidence.empty")),
    );
    return;
  }
  root.replaceChildren(...cards);
}

function renderPosture(posture = {}, gate = {}) {
  const root = document.querySelector("#market-posture");
  const coverage = Number(posture.coverage || 0);
  const gateState = gate.state || "missing";
  const marketState = gate.market_state || "unavailable";
  root.replaceChildren(
    scoreBlock(
      t("market.gate.title"),
      localized(`market.gate.${gateState}`, gateState).replace(
        "{regime}",
        localized(`market.gate.regime.${marketState}`, marketState),
      ),
      t("market.gate.explanation"),
    ),
    scoreBlock(
      t("market.gate.regimeLabel"),
      localized(`market.gate.regime.${marketState}`, marketState),
      t("market.gate.memory", {
        count: gate.values?.distribution_days ?? "—",
      }),
    ),
    scoreBlock(
      t("market.score"),
      formatScore(posture.score),
      unavailableText(posture.unavailable_reason),
    ),
    scoreBlock(
      t("market.coverage"),
      formatPercent(coverage),
      t("market.coverage.help"),
    ),
    scoreBlock(
      t("market.evidenceCount"),
      String(posture.evidence?.length || 0),
      t("market.evidenceCount.help"),
    ),
  );
  text(document.querySelector("#market-coverage"), formatPercent(coverage));
}

function renderMacroRisk(macro = {}) {
  const root = document.querySelector("#macro-risk");
  const stateLabel = localized(
    `market.macro.state.${macro.state || "unavailable"}`,
    macro.state || "—",
  );
  const cards = [
    scoreBlock(
      t("market.macro.total"),
      formatScore(macro.score),
      macro.score == null
        ? unavailableText(macro.unavailable_reason)
        : `${stateLabel} · ${t("market.coverage")} ${formatPercent(macro.coverage)}`,
    ),
  ];
  for (const key of [
    "rates",
    "inflation_energy",
    "credit_liquidity",
    "risk_aversion",
  ]) {
    const component = macro.components?.[key] || {};
    cards.push(
      scoreBlock(
        localized(`market.macro.component.${key}`, key),
        formatScore(component.score),
        `${t("market.coverage")} ${formatPercent(component.coverage)}`,
      ),
    );
  }
  root.replaceChildren(...cards);
}

function sectorButton(row) {
  const button = element("button", "sector-tile");
  button.type = "button";
  button.dataset.sector = row.key;
  button.setAttribute("aria-pressed", String(row.key === state.sector));
  const label = text(
    element("span"),
    localized(row.label_key, row.key),
  );
  const relative = text(
    element("strong"),
    formatPercent(row.relative_return),
  );
  const risk = text(
    element("small"),
    `${t("market.risk")} ${formatScore(riskDisplayScore(row.downside_risk))}`,
  );
  button.append(label, relative, risk);
  return button;
}

function renderSectorHeatmap(rows = [], themeGroups = []) {
  const root = document.querySelector("#sector-heatmap");
  const grid = element("div", "sector-heatmap-grid");
  grid.append(
    ...themeGroups.map(sectorButton),
    ...rows.map(sectorButton),
  );
  root.replaceChildren(grid);
}

function helpMarker(key) {
  const help = element("button", "evidence-help");
  help.type = "button";
  text(help, "?");
  const explanation = localized(
    `market.evidence.help.${key}`,
    t("market.evidence.help.default"),
  );
  help.dataset.help = explanation;
  help.title = explanation;
  help.setAttribute("aria-label", explanation);
  return help;
}

function renderEvidence(evidence = []) {
  const root = document.querySelector("#market-evidence");
  if (!evidence.length) {
    root.replaceChildren(
      text(element("p", "market-empty"), t("market.evidence.empty")),
    );
    return;
  }
  const list = element("ul", "market-evidence-list");
  for (const row of evidence) {
    const item = element("li");
    const heading = element("strong");
    text(heading, localized(`market.evidence.${row.key}`, row.key));
    const detail = element("span", "market-evidence-detail");
    text(
      detail,
      `${localized(`market.state.${row.state}`, row.state)} · `
      + `${t("market.value")} ${row.value ?? "—"} · `
      + `${t("market.threshold")} ${row.threshold ?? "—"} · ${row.window}`,
    );
    const missing = element("span", "market-unavailable-reason");
    text(missing, row.unavailable_reason
      ? unavailableText(row.unavailable_reason)
      : "");
    item.append(heading, detail, missing, helpMarker(row.key));
    list.append(item);
  }
  root.replaceChildren(list);
}

function tableHeader(keys) {
  const head = document.createElement("thead");
  const row = document.createElement("tr");
  for (const key of keys) {
    row.append(text(document.createElement("th"), t(key)));
  }
  head.append(row);
  return head;
}

function renderDrilldown(group = {}, constituents = []) {
  const root = document.querySelector("#sector-drilldown");
  const summary = element("div", "market-drilldown-summary");
  summary.append(
    scoreBlock(
      localized(group.label_key, group.key),
      formatPercent(group.returns?.[String(state.horizon)]),
      `${t("market.coverage")} ${formatPercent(group.coverage)}`,
    ),
    scoreBlock(
      t("market.opportunity"),
      formatScore(group.reversal_opportunity?.score),
      unavailableText(group.reversal_opportunity?.unavailable_reason),
    ),
    scoreBlock(
      t("market.risk"),
      formatScore(riskDisplayScore(group.downside_risk)),
      riskDetail(group.downside_risk),
    ),
  );
  if (!constituents.length) {
    root.replaceChildren(
      summary,
      text(element("p", "market-empty"), t("market.drilldown.empty")),
    );
    return;
  }
  const scroll = element("div", "market-table-scroll");
  const table = element("table", "market-table");
  table.append(
    tableHeader([
      "market.column.ticker",
      "market.column.classification",
      "market.column.relativeStrength",
      "market.column.opportunity",
      "market.column.risk",
      "market.column.pressure",
      "market.column.date",
    ]),
  );
  const body = document.createElement("tbody");
  for (const row of constituents) {
    const tr = document.createElement("tr");
    const link = document.createElement("a");
    link.href = `/?ticker=${encodeURIComponent(row.ticker)}`;
    text(link, row.ticker);
    const values = [
      link,
      localized(`market.classification.${row.classification}`, row.classification),
      formatPercent(row.relative_strength_20),
      formatScore(row.reversal_opportunity?.score),
      riskCellText(row.downside_risk),
      localized(`market.pressure.${row.pressure_state}`, row.pressure_state),
      row.observation_date,
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      if (value instanceof Node) cell.append(value);
      else text(cell, value);
      tr.append(cell);
    }
    body.append(tr);
  }
  table.append(body);
  scroll.append(table);
  root.replaceChildren(summary, scroll);
}

function renderEvents(events = []) {
  const root = document.querySelector("#market-events");
  if (!events.length) {
    root.replaceChildren(
      text(element("p", "market-empty"), t("market.events.empty")),
    );
    return;
  }
  const list = element("ul", "market-events-list");
  for (const event of events) {
    const item = document.createElement("li");
    const heading = element("strong");
    text(
      heading,
      `${event.ticker || event.source} · `
      + localized(`market.evidence.${event.key}`, event.key),
    );
    const detail = element("span", "market-evidence-detail");
    text(
      detail,
      `${event.previous_value ?? "—"} → ${event.current_value ?? "—"} · `
      + `${event.observation_date || "—"}`,
    );
    item.append(heading, detail);
    list.append(item);
  }
  root.replaceChildren(list);
}

function render(payload) {
  state.payload = payload;
  renderPosture(payload.market_posture, payload.market_gate);
  renderReferenceFactors(payload.market_posture?.evidence || []);
  renderMacroRisk(payload.macro_risk);
  renderSectorHeatmap(
    payload.sectors || [],
    payload.theme_groups || [],
  );
  renderEvidence(payload.market_posture?.evidence || []);
  renderDrilldown(payload.selected_group, payload.constituents);
  renderEvents(payload.changed_events);
  text(document.querySelector("#market-asof"), payload.asof || "—");
  text(
    document.querySelector("#market-data-tier"),
    localized(`market.tier.${payload.evidence_tier}`, payload.evidence_tier),
  );
}

function setStatus(message, tone = "") {
  const target = document.querySelector("#market-status");
  text(target, message);
  if (tone) target.dataset.tone = tone;
  else delete target.dataset.tone;
}

function renderStatus() {
  if (state.status.kind === "loading") {
    setStatus(t("market.loading"));
    return;
  }
  if (state.status.kind === "error") {
    setStatus(translateError(state.status.error), "error");
    return;
  }
  setStatus("");
}

function renderMacroHistoryStatus() {
  const target = document.querySelector("#macro-history-status");
  const status = state.macroHistory.status;
  if (status.kind === "loading") {
    text(target, t("market.macro.history.loading"));
    delete target.dataset.tone;
    return;
  }
  if (status.kind === "error") {
    text(target, translateError(status.error));
    target.dataset.tone = "error";
    return;
  }
  const payload = state.macroHistory.payload;
  if (payload?.unavailable_reason) {
    text(target, unavailableText(payload.unavailable_reason));
    target.dataset.tone = "error";
    return;
  }
  text(
    target,
    payload
      ? t("market.macro.history.loaded", {
        count: payload.rows?.length || 0,
        asof: payload.asof || "—",
      })
      : "",
  );
  delete target.dataset.tone;
}

async function loadMacroHistory() {
  const requestId = ++state.macroHistory.requestId;
  state.macroHistory.status = { kind: "loading", error: null };
  renderMacroHistoryStatus();
  try {
    const payload = await getMacroHistory({
      range: state.macroHistory.range,
      benchmark: state.macroHistory.benchmark,
    });
    if (requestId !== state.macroHistory.requestId) return;
    state.macroHistory.payload = payload;
    macroHistoryCharts?.update(payload);
    state.macroHistory.status = { kind: "idle", error: null };
    renderMacroHistoryStatus();
  } catch (error) {
    if (requestId !== state.macroHistory.requestId) return;
    state.macroHistory.status = { kind: "error", error };
    renderMacroHistoryStatus();
  }
}

async function load() {
  const requestId = ++state.requestId;
  state.status = { kind: "loading", error: null };
  renderStatus();
  try {
    const payload = await getMarketOverview({
      horizon: state.horizon,
      sector: state.sector,
    });
    if (requestId !== state.requestId) return;
    render(payload);
    state.status = { kind: "idle", error: null };
    renderStatus();
  } catch (error) {
    if (requestId !== state.requestId) return;
    state.status = { kind: "error", error };
    renderStatus();
  }
}

document.querySelector("#sector-heatmap").addEventListener("click", (event) => {
  const button = event.target.closest("[data-sector]");
  if (!button || button.dataset.sector === state.sector) return;
  state.sector = button.dataset.sector;
  load();
});

for (const control of document.querySelectorAll("[data-horizon]")) {
  control.addEventListener("click", () => {
    const horizon = Number(control.dataset.horizon);
    if (horizon === state.horizon) return;
    state.horizon = horizon;
    for (const button of document.querySelectorAll("[data-horizon]")) {
      button.setAttribute(
        "aria-pressed",
        String(Number(button.dataset.horizon) === horizon),
      );
    }
    load();
  });
}

for (const control of document.querySelectorAll("[data-macro-range]")) {
  control.addEventListener("click", () => {
    const range = control.dataset.macroRange;
    if (range === state.macroHistory.range) return;
    state.macroHistory.range = range;
    macroHistoryCharts?.resetSelection();
    for (const button of document.querySelectorAll("[data-macro-range]")) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.macroRange === range),
      );
    }
    loadMacroHistory();
  });
}

for (const control of document.querySelectorAll("[data-macro-benchmark]")) {
  control.addEventListener("click", () => {
    const benchmark = control.dataset.macroBenchmark;
    if (benchmark === state.macroHistory.benchmark) return;
    state.macroHistory.benchmark = benchmark;
    macroHistoryCharts?.resetSelection();
    for (const button of document.querySelectorAll("[data-macro-benchmark]")) {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.macroBenchmark === benchmark),
      );
    }
    loadMacroHistory();
  });
}

for (const control of document.querySelectorAll("[data-locale]")) {
  control.addEventListener("click", () => setLocale(control.dataset.locale));
}

subscribeLocale((locale) => {
  applyDocumentLocale(document, locale);
  if (state.payload) {
    render(state.payload);
  }
  macroHistoryCharts?.setLocale(locale);
  renderStatus();
  renderMacroHistoryStatus();
});

applyDocumentLocale(document, getLocale());
try {
  macroHistoryCharts = createMacroHistoryCharts({
    scoreElement: document.querySelector("#macro-history-score-chart"),
    contextElement: document.querySelector("#macro-history-context-chart"),
    detailElement: document.querySelector("#macro-history-detail"),
    seriesSelect: document.querySelector("#macro-history-series"),
    unlockButton: document.querySelector("#macro-history-unlock"),
    translate: t,
    locale: getLocale(),
  });
} catch (error) {
  state.macroHistory.status = { kind: "error", error };
  renderMacroHistoryStatus();
}
load();
loadMacroHistory();
