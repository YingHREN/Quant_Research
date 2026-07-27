import { api } from "./api.js";
import { createCacheStatusController } from "./cache_status.js";
import { createLinkedCharts } from "./charts.js";
import { renderFactors, renderStructures } from "./factors.js";
import {
  applyDocumentLocale,
  getLocale,
  setLocale,
  subscribeLocale,
  t,
  translateError,
} from "./i18n.js";
import {
  MARKER_LAYER_DEFINITIONS,
  MARKER_LAYER_PRESETS,
  normalizeMarkerLayers,
  persistMarkerLayers,
  readMarkerLayers,
} from "./marker_layers.js";
import { renderScenarios } from "./scenarios.js";
import {
  chooseInitialTicker,
  persistSelectedTicker,
  readRequestedTicker,
  readStoredTicker,
  store,
} from "./store.js";
import {
  classificationFor,
  filterTickers,
  renderUniverse,
  sortTickers,
} from "./universe.js";
import {
  createUpdateController,
  shouldReloadAfterUpdate,
  shouldReloadSelectedTicker,
} from "./update.js";
import { createResearchPoolControl } from "./research_pool_control.js";
import { createIntradayLiveController } from "./intraday-live.js";

const elements = {};
let stockRequestSequence = 0;
let chartController = null;
let updateController = null;
let cacheStatusController = null;
let researchPoolController = null;
let intradayLiveController = null;
let unsubscribeLocale = null;
let universeError = null;
let researchError = null;
let selectedMarkerLayers = readMarkerLayers();

function byId(id) {
  return document.getElementById(id);
}

function setText(element, value) {
  element.textContent = value == null || value === "" ? "—" : String(value);
}

function setRecoveryControl(
  element,
  { visible = false, loading = false, labelKey = "recovery.stock" } = {},
) {
  if (!element) return;
  element.hidden = !visible;
  element.disabled = loading;
  element.dataset.loading = String(loading);
  setText(
    element,
    t(loading ? "recovery.loading" : labelKey, {}, store.getState().locale),
  );
}

function formatNumber(value) {
  return Number.isFinite(value)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)
    : "—";
}

export function formatDailyReturn(value, unit = "fraction") {
  if (!Number.isFinite(value)) return "—";
  const percent = unit === "fraction" ? value * 100 : value;
  const sign = percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(2)}%`;
}

export function clearStockQuote(fields) {
  for (const key of ["selectedClose", "selectedChange", "observationDate"]) {
    if (fields && fields[key]) fields[key].textContent = "—";
  }
}

function errorState(error) {
  return {
    code: error && typeof error.code === "string" ? error.code : "",
    message: error && typeof error.message === "string" ? error.message : "",
  };
}

function errorText(error, locale = getLocale()) {
  return translateError(error, "request.failed", locale);
}

function currentRows() {
  const state = store.getState();
  return sortTickers(
    filterTickers(state.universe, state.query, state.filters),
    state.sortKey,
    state.sortDirection,
  );
}

function sectorLabel(sectorKey, locale = store.getState().locale) {
  if (!sectorKey) return t("universe.sector.unclassified", {}, locale);
  const key = `market.sector.${sectorKey}`;
  const localized = t(key, {}, locale);
  return localized === key ? String(sectorKey).replaceAll("_", " ") : localized;
}

function appendClassificationText(parent, className, value) {
  const node = document.createElement("span");
  node.className = className;
  node.textContent = value;
  parent.append(node);
  return node;
}

function classificationCard(titleKey, classification, kind, locale) {
  const card = document.createElement("article");
  card.className = `classification-card classification-card-${kind}`;
  const heading = document.createElement("h3");
  heading.textContent = t(titleKey, {}, locale);
  card.append(heading);
  const sector = document.createElement("strong");
  sector.className = "classification-sector";
  sector.textContent = sectorLabel(classification?.sector_key, locale);
  card.append(sector);
  if (!classification) {
    card.dataset.state = "unclassified";
    return card;
  }
  const metadata = document.createElement("div");
  metadata.className = "classification-metadata";
  if (Number.isFinite(classification.confidence)) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t(
        "classification.confidence",
        { value: `${Math.round(classification.confidence * 100)}%` },
        locale,
      ),
    );
  }
  if (classification.benchmark_ticker) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t(
        "classification.benchmark",
        { ticker: classification.benchmark_ticker },
        locale,
      ),
    );
  }
  if (Number.isFinite(classification.common_days)) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t("classification.commonDays", { days: classification.common_days }, locale),
    );
  }
  if (Number.isFinite(classification.residual_correlation)) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t(
        "classification.correlation",
        { value: classification.residual_correlation.toFixed(2) },
        locale,
      ),
    );
  }
  if (Number.isFinite(classification.residual_beta)) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t(
        "classification.beta",
        { value: classification.residual_beta.toFixed(2) },
        locale,
      ),
    );
  }
  if (Number.isFinite(classification.relative_return_63d)) {
    appendClassificationText(
      metadata,
      "classification-chip",
      t(
        "classification.relativeReturn",
        { value: formatDailyReturn(classification.relative_return_63d) },
        locale,
      ),
    );
  }
  card.append(metadata);
  const source = document.createElement("small");
  source.className = "classification-source";
  source.textContent = t(
    "classification.source",
    {
      source: classification.source || "—",
      version: classification.rule_version || "—",
    },
    locale,
  );
  card.append(source);
  return card;
}

export function renderSecurityClassification(
  ticker = store.getState().selectedTicker,
  locale = store.getState().locale,
) {
  const container = elements.securityClassification;
  if (!container) return;
  const row = store.getState().universe.find((item) => item.ticker === ticker);
  const classification = row?.sector_classification;
  if (!classification) {
    const empty = document.createElement("p");
    empty.className = "secondary-copy";
    empty.textContent = t("classification.empty", {}, locale);
    container.replaceChildren(empty);
    container.dataset.state = "unclassified";
    return;
  }
  const stateKeys = {
    agree: "classification.agree",
    conflict: "classification.conflict",
    sec_only: "classification.secOnly",
    behavior_only: "classification.behaviorOnly",
    unclassified: "classification.stateUnclassified",
  };
  const heading = document.createElement("div");
  heading.className = "classification-heading";
  const title = document.createElement("strong");
  title.textContent = t(
    stateKeys[classification.state] || "classification.stateUnclassified",
    {},
    locale,
  );
  heading.append(title);
  const grid = document.createElement("div");
  grid.className = "classification-grid";
  grid.append(
    classificationCard(
      "classification.secHeading",
      classification.sec,
      "sec",
      locale,
    ),
    classificationCard(
      "classification.behaviorHeading",
      classification.market_behavior,
      "behavior",
      locale,
    ),
  );
  const children = [heading, grid];
  if (
    classification.state === "conflict"
    && classification.sec
    && classification.market_behavior
  ) {
    const reason = document.createElement("p");
    reason.className = "classification-reason";
    reason.textContent = t(
      "classification.conflictReason",
      {
        sec: sectorLabel(classification.sec.sector_key, locale),
        behavior: sectorLabel(
          classification.market_behavior.sector_key,
          locale,
        ),
        benchmark: classification.market_behavior.benchmark_ticker || "—",
      },
      locale,
    );
    children.push(reason);
  }
  container.replaceChildren(...children);
  container.dataset.state = classification.state || "unclassified";
}

export function renderSecurityRelativeStrength(
  ticker = store.getState().selectedTicker,
  locale = store.getState().locale,
) {
  const element = elements.securityRsState;
  if (!element) return;
  const row = store.getState().universe.find((item) => item.ticker === ticker);
  const rating = row?.rs_rating;
  setText(
    element,
    Number.isFinite(rating)
      ? t("universe.rs.value", { rating }, locale)
      : t("universe.rs.unavailable", {}, locale),
  );
  element.dataset.tone = Number.isFinite(rating)
    ? rating >= 90 ? "current" : rating >= 80 ? "watch" : "neutral"
    : "unavailable";
  element.setAttribute(
    "title",
    Number.isFinite(rating)
      ? t(
        "universe.rs.detail",
        {
          date: row.rs_asof || "—",
          sample: row.rs_sample_count ?? "—",
          model: row.rs_model_version || "—",
        },
        locale,
      )
      : t("universe.rs.disclaimer", {}, locale),
  );
}

function renderSectorControls(rows, state) {
  const taxonomy = state.filters.sectorTaxonomy || "sec";
  const selectedSector = state.filters.sectorKey || "all";
  const summary = state.universePayload?.classification_summary;
  const counts = summary?.sector_counts?.[taxonomy] || {};
  elements.sectorTaxonomy.value = taxonomy;
  const options = [];
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = t("universe.sector.all", {}, state.locale);
  options.push(all);
  Object.keys(counts).sort().forEach((sectorKey) => {
    const option = document.createElement("option");
    option.value = sectorKey;
    option.textContent = sectorKey === "unclassified"
      ? t("universe.sector.unclassified", {}, state.locale)
      : sectorLabel(sectorKey, state.locale);
    options.push(option);
  });
  elements.sectorKey.replaceChildren(...options);
  elements.sectorKey.value = Object.hasOwn(counts, selectedSector)
    ? selectedSector
    : "all";
  if (!summary || summary.status !== "available") {
    setText(
      elements.sectorMembershipSummary,
      t("universe.sector.unavailable", {}, state.locale),
    );
    elements.sectorMembershipSummary.dataset.tone = "warning";
    return;
  }
  elements.sectorMembershipSummary.removeAttribute("data-tone");
  const researchCount = selectedSector === "all"
    ? summary.research_universe_count
    : counts[selectedSector] || 0;
  setText(
    elements.sectorMembershipSummary,
    t(
      selectedSector === "all"
        ? "universe.sector.membership"
        : "universe.sector.selectedMembership",
      { research: researchCount, local: rows.length },
      state.locale,
    ),
  );
}

function checkedMarkerLayers() {
  return elements.markerLayerControls
    .filter((control) => control.checked)
    .map((control) => control.dataset.markerLayer);
}

function syncMarkerLayerControls(layers, locale = store.getState().locale) {
  selectedMarkerLayers = normalizeMarkerLayers(layers);
  const selected = new Set(selectedMarkerLayers);
  elements.markerLayerControls.forEach((control) => {
    control.checked = selected.has(control.dataset.markerLayer);
  });
  setText(
    elements.markerLayerCount,
    t(
      "chart.layers.summary",
      { selected: selectedMarkerLayers.length, total: MARKER_LAYER_DEFINITIONS.length },
      locale,
    ),
  );
}

function applyMarkerLayers(layers) {
  selectedMarkerLayers = chartController.setMarkerLayers(layers);
  persistMarkerLayers(selectedMarkerLayers);
  syncMarkerLayerControls(selectedMarkerLayers);
}

function paintUniverse() {
  const state = store.getState();
  const rows = currentRows();
  renderUniverse(elements.universeList, rows, {
    selectedTicker: state.selectedTicker,
    onSelect: selectTicker,
    locale: state.locale,
    sectorTaxonomy: state.filters.sectorTaxonomy || "sec",
  });
  renderSectorControls(rows, state);
  renderSecurityClassification(state.selectedTicker, state.locale);
  renderSecurityRelativeStrength(state.selectedTicker, state.locale);
  setText(elements.universeCount, `${rows.length}/${state.universe.length}`);
  setText(
    elements.universeStatus,
    (universeError && errorText(universeError, state.locale)) || (state.universe.length
      ? t("universe.shown", { shown: rows.length, total: state.universe.length }, state.locale)
      : t("universe.none", {}, state.locale)),
  );
  if (universeError) elements.universeStatus.dataset.tone = "error";
  else elements.universeStatus.removeAttribute("data-tone");
}

function renderWarnings(warnings) {
  elements.dataWarnings.replaceChildren();
  const fragment = document.createDocumentFragment();
  warnings.forEach((warning) => {
    const item = document.createElement("li");
    const key = `warning.${warning}`;
    const localized = t(key, {}, store.getState().locale);
    item.textContent = localized === key ? String(warning).replaceAll("_", " ") : localized;
    fragment.append(item);
  });
  elements.dataWarnings.append(fragment);
}

function renderTopRiskBadge(topRisk, element, locale) {
  if (!element) return;
  const latest = topRisk?.status === "available" ? topRisk.latest : null;
  if (!latest || !Number.isFinite(latest.score)) {
    setText(element, t("topRisk.badge.unavailable", {}, locale));
    element.dataset.tone = "unavailable";
    return;
  }
  const stateKey = `topRisk.state.${latest.state}`;
  const localizedState = t(stateKey, {}, locale);
  setText(
    element,
    t(
      "topRisk.badge.value",
      {
        score: Math.round(latest.score),
        state: localizedState === stateKey
          ? t("topRisk.state.unavailable", {}, locale)
          : localizedState,
      },
      locale,
    ),
  );
  element.dataset.tone = latest.state;
}

function renderStockHeader(payload) {
  const summary = payload.summary || {};
  const locale = store.getState().locale;
  setText(elements.selectedTicker, payload.ticker);
  setText(elements.selectedClose, formatNumber(summary.close));
  setText(
    elements.selectedChange,
    formatDailyReturn(summary.daily_return, summary.daily_return_unit),
  );
  setText(elements.observationDate, payload.observation_date);
  setText(
    elements.securityState,
    t(
      summary.inactive
        ? "security.state.inactive"
        : summary.stale ? "security.state.stale" : "security.state.current",
      {},
      locale,
    ),
  );
  setText(
    elements.researchStatus,
    t(
      "security.loaded",
      { date: payload.observation_date || t("security.unknownDate", {}, locale) },
      locale,
    ),
  );
  const universeRow = store.getState().universe.find(
    (row) => row.ticker === payload.ticker,
  );
  const membership = universeRow?.pool_membership || payload.pool_membership || {};
  const pool = membership.active && membership.research
    ? "both"
    : membership.research
      ? "research"
      : membership.research_catalog && !membership.active
        ? "catalog"
        : "active";
  setText(elements.securityPoolState, t(`universe.pool.${pool}`, {}, locale));
  elements.securityPoolState.dataset.tone = pool === "research" ? "watch" : "neutral";
  researchPoolController?.setSelection(payload.ticker, membership);
  const gate = payload.technical_gate || {};
  const gateState = ["pass", "fail", "missing"].includes(gate.state)
    ? gate.state
    : "missing";
  setText(
    elements.securityGateState,
    Number.isFinite(gate.passed_conditions)
      ? t(
        "universe.gate.score",
        {
          passed: gate.passed_conditions,
          total: gate.condition_count ?? 4,
        },
        locale,
      )
      : t(`universe.gate.${gateState}`, {}, locale),
  );
  elements.securityGateState.dataset.tone = gateState === "pass"
    ? "current"
    : gateState === "fail" ? "danger" : "unavailable";
  elements.securityGateState.setAttribute(
    "title",
    t("universe.gate.explanation", {}, locale),
  );
  const marketGate = payload.market_gate || {};
  const marketGateState = ["pass", "fail", "missing"].includes(marketGate.state)
    ? marketGate.state
    : "missing";
  const regimeKey = `market.gate.regime.${marketGate.market_state || "unavailable"}`;
  const regimeLabel = t(regimeKey, {}, locale);
  if (elements.marketRegimeGateState) {
    setText(
      elements.marketRegimeGateState,
      t(
        `market.gate.${marketGateState}`,
        {
          regime: regimeLabel === regimeKey
            ? marketGate.market_state || "—"
            : regimeLabel,
        },
        locale,
      ),
    );
    elements.marketRegimeGateState.dataset.tone = marketGateState === "pass"
      ? "current"
      : marketGateState === "fail" ? "danger" : "unavailable";
    elements.marketRegimeGateState.setAttribute(
      "title",
      t("market.gate.explanation", {}, locale),
    );
  }
  renderTopRiskBadge(payload.top_risk, elements.topRiskState, locale);
  renderWarnings(Array.isArray(payload.warnings) ? payload.warnings : []);
}

function factorRenderOptions() {
  return {
    overview: elements.factorOverview,
    tableBody: elements.factorTableBody,
    groupMetadata: store.getState().universePayload?.factor_groups,
    locale: store.getState().locale,
  };
}

function clearResearchPanels() {
  chartController.setChartData({ chart: [] });
  renderFactors([], factorRenderOptions());
  renderStructures(null, elements.structureContent, store.getState().locale);
  renderScenarios(null, {
    chart: elements.scenarioChart,
    metadata: elements.scenarioMeta,
    locale: store.getState().locale,
  });
}

async function selectTicker(ticker) {
  if (!ticker) return;
  const requestSequence = ++stockRequestSequence;
  const previousState = store.getState();
  const preserveExistingDetails = (
    previousState.selectedTicker === ticker
    && previousState.stockPayload
  );
  researchError = null;
  store.setState({
    selectedTicker: ticker,
    stockPayload: preserveExistingDetails ? previousState.stockPayload : null,
  });
  intradayLiveController?.selectTicker(ticker);
  persistSelectedTicker(ticker);
  paintUniverse();
  setRecoveryControl(elements.stockRetry, {
    visible: false,
    loading: true,
    labelKey: "recovery.stock",
  });
  setText(elements.selectedTicker, ticker);
  const locale = store.getState().locale;
  setText(elements.securityState, t("security.state.loading", {}, locale));
  setText(elements.researchStatus, t("security.loading", { ticker }, locale));
  elements.researchStatus.removeAttribute("data-tone");
  if (!preserveExistingDetails) {
    renderTopRiskBadge(null, elements.topRiskState, locale);
    clearStockQuote(elements);
    renderWarnings([]);
    clearResearchPanels();
  }

  try {
    const payload = await api.getStock(ticker);
    if (requestSequence !== stockRequestSequence) return;
    store.setState({ stockPayload: payload });
    researchError = null;
    setRecoveryControl(elements.stockRetry, {
      visible: false,
      labelKey: "recovery.stock",
    });
    renderStockHeader(payload);
    chartController.setChartData(payload);
    renderFactors(payload.factors, factorRenderOptions());
    renderStructures(payload.structures, elements.structureContent, store.getState().locale);
    renderScenarios(payload.scenarios, {
      chart: elements.scenarioChart,
      metadata: elements.scenarioMeta,
      locale: store.getState().locale,
    });
  } catch (error) {
    if (requestSequence !== stockRequestSequence) return;
    researchError = errorState(error);
    setText(elements.securityState, t("security.state.unavailable", {}, store.getState().locale));
    renderTopRiskBadge(null, elements.topRiskState, store.getState().locale);
    setText(elements.researchStatus, errorText(researchError, store.getState().locale));
    elements.researchStatus.dataset.tone = "error";
    setRecoveryControl(elements.stockRetry, {
      visible: true,
      labelKey: "recovery.stock",
    });
  }
}

function coverageText(payload) {
  const buckets = payload && payload.freshness && payload.freshness.by_date;
  const current = Array.isArray(buckets) && buckets.length ? buckets[0].tickers : 0;
  const total = Array.isArray(payload.tickers) ? payload.tickers.length : 0;
  return t("header.coverageValue", { current, total }, store.getState().locale);
}

async function loadUniverse() {
  setRecoveryControl(elements.universeRetry, {
    visible: false,
    loading: true,
    labelKey: "recovery.universe",
  });
  setText(elements.universeStatus, t("universe.loading", {}, store.getState().locale));
  try {
    const payload = await api.getUniverse();
    universeError = null;
    const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
    const selectedTicker = chooseInitialTicker(
      rows,
      readStoredTicker(),
      readRequestedTicker(),
    );
    store.setState({ universePayload: payload, universe: rows, selectedTicker });
    setRecoveryControl(elements.universeRetry, {
      visible: false,
      labelKey: "recovery.universe",
    });
    setText(elements.marketDate, payload.asof || t("header.noData", {}, store.getState().locale));
    setText(elements.marketCoverage, coverageText(payload));
    paintUniverse();
    if (selectedTicker) await selectTicker(selectedTicker);
  } catch (error) {
    universeError = errorState(error);
    const state = store.getState();
    const hasSuccessfulUniverse = state.universe.length > 0 && state.universePayload;
    if (!hasSuccessfulUniverse) {
      store.setState({ universe: [], universePayload: null, selectedTicker: null });
    }
    paintUniverse();
    setRecoveryControl(elements.universeRetry, {
      visible: true,
      labelKey: "recovery.universe",
    });
    elements.universeStatus.dataset.tone = "error";
    if (!hasSuccessfulUniverse) {
      setText(
        elements.securityState,
        t("security.state.unavailable", {}, store.getState().locale),
      );
      renderTopRiskBadge(null, elements.topRiskState, store.getState().locale);
      setText(
        elements.researchStatus,
        t("security.unavailableUntilUniverse", {}, store.getState().locale),
      );
      elements.researchStatus.dataset.tone = "error";
    }
  }
}

async function refreshUniverseAfterUpdate(updateSnapshot = {}) {
  const previous = store.getState();
  const previousTicker = previous.selectedTicker;
  const previousObservationDate = previous.stockPayload?.observation_date || null;
  try {
    const payload = await api.getUniverse();
    universeError = null;
    const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
    const selectedTicker = chooseInitialTicker(rows, previousTicker);
    store.setState({ universePayload: payload, universe: rows, selectedTicker });
    setText(elements.marketDate, payload.asof || t("header.noData", {}, store.getState().locale));
    setText(elements.marketCoverage, coverageText(payload));
    paintUniverse();

    const selectionChanged = selectedTicker !== previousTicker;
    const observationChanged = shouldReloadSelectedTicker(
      selectedTicker,
      previousObservationDate,
      rows,
    );
    if (
      selectedTicker
      && shouldReloadAfterUpdate(updateSnapshot, selectionChanged, observationChanged)
    ) {
      await selectTicker(selectedTicker);
    }
  } catch (error) {
    universeError = errorState(error);
    setText(elements.universeStatus, errorText(universeError, store.getState().locale));
    elements.universeStatus.dataset.tone = "error";
  } finally {
    await cacheStatusController?.refresh();
  }
}

async function refreshUniverseAfterResearchPoolChange(change) {
  const previous = store.getState();
  const payload = await api.getUniverse();
  universeError = null;
  const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
  const selectedTicker = previous.selectedTicker;
  const selectedRow = rows.find((row) => row.ticker === selectedTicker) || null;
  const stockPayload = (
    previous.stockPayload
    && previous.stockPayload.ticker === selectedTicker
    && selectedRow
  )
    ? {
      ...previous.stockPayload,
      pool_membership: selectedRow.pool_membership,
    }
    : previous.stockPayload;
  store.setState({
    universePayload: payload,
    universe: rows,
    selectedTicker,
    stockPayload,
  });
  setText(
    elements.marketDate,
    payload.asof || t("header.noData", {}, store.getState().locale),
  );
  setText(elements.marketCoverage, coverageText(payload));
  paintUniverse();
  if (stockPayload) renderStockHeader(stockPayload);
  return change;
}

function bindControls() {
  elements.universeRetry.addEventListener("click", () => {
    void loadUniverse();
  });
  elements.stockRetry.addEventListener("click", () => {
    const ticker = store.getState().selectedTicker;
    if (ticker) void selectTicker(ticker);
  });
  elements.universeSearch.addEventListener("input", (event) => {
    store.setState({ query: event.currentTarget.value });
    paintUniverse();
  });
  elements.sortKey.addEventListener("change", (event) => {
    store.setState({ sortKey: event.currentTarget.value });
    paintUniverse();
  });
  elements.sortDirection.addEventListener("click", () => {
    const direction = store.getState().sortDirection === "asc" ? "desc" : "asc";
    store.setState({ sortDirection: direction });
    const locale = store.getState().locale;
    setText(
      elements.sortDirection,
      t(`universe.sort.${direction === "asc" ? "ascending" : "descending"}`, {}, locale),
    );
    elements.sortDirection.setAttribute(
      "aria-label",
      t(`universe.sort.${direction === "asc" ? "ascendingAria" : "descendingAria"}`, {}, locale),
    );
    paintUniverse();
  });
  elements.sectorTaxonomy.addEventListener("change", (event) => {
    store.setState({
      filters: {
        ...store.getState().filters,
        sectorTaxonomy: event.currentTarget.value,
        sectorKey: "all",
      },
    });
    paintUniverse();
  });
  elements.sectorKey.addEventListener("change", (event) => {
    store.setState({
      filters: {
        ...store.getState().filters,
        sectorKey: event.currentTarget.value,
      },
    });
    paintUniverse();
  });
  document.querySelectorAll("[data-filter]").forEach((control) => {
    control.addEventListener("change", () => {
      const filters = { ...store.getState().filters, [control.dataset.filter]: control.checked };
      store.setState({ filters });
      paintUniverse();
    });
  });
  elements.rangeControls.forEach((control) => {
    control.addEventListener("click", () => {
      elements.rangeControls.forEach((button) => {
        button.setAttribute("aria-pressed", String(button === control));
      });
      chartController.setRange(control.dataset.range);
    });
  });
  elements.forecastControls.forEach((control) => {
    control.addEventListener("click", () => {
      const horizon = chartController.setForecastHorizon(control.dataset.forecastHorizon);
      elements.forecastControls.forEach((button) => {
        button.setAttribute("aria-pressed", String(Number(button.dataset.forecastHorizon) === horizon));
      });
    });
  });
  elements.markerLayerControls.forEach((control) => {
    control.addEventListener("change", () => applyMarkerLayers(checkedMarkerLayers()));
  });
  elements.markerPresetControls.forEach((control) => {
    control.addEventListener("click", () => {
      applyMarkerLayers(MARKER_LAYER_PRESETS[control.dataset.markerPreset]);
    });
  });
  elements.localeControls.forEach((control) => {
    control.addEventListener("click", () => setLocale(control.dataset.locale));
  });
}

function applyLocale(locale) {
  applyDocumentLocale(document, locale);
  store.setState({ locale });
  const state = store.getState();
  const direction = state.sortDirection;
  setText(
    elements.sortDirection,
    t(`universe.sort.${direction === "asc" ? "ascending" : "descending"}`, {}, locale),
  );
  elements.sortDirection.setAttribute(
    "aria-label",
    t(`universe.sort.${direction === "asc" ? "ascendingAria" : "descendingAria"}`, {}, locale),
  );
  paintUniverse();
  researchPoolController?.setLocale(locale);
  intradayLiveController?.render();
  chartController?.setLocale(locale);
  syncMarkerLayerControls(selectedMarkerLayers, locale);
  if (state.universePayload) {
    setText(elements.marketDate, state.universePayload.asof || t("header.noData", {}, locale));
    setText(elements.marketCoverage, coverageText(state.universePayload));
  }
  if (state.stockPayload) {
    renderStockHeader(state.stockPayload);
    renderFactors(state.stockPayload.factors, factorRenderOptions());
    renderStructures(state.stockPayload.structures, elements.structureContent, locale);
    renderScenarios(state.stockPayload.scenarios, {
      chart: elements.scenarioChart,
      metadata: elements.scenarioMeta,
      locale,
    });
  } else if (universeError) {
    setText(elements.securityState, t("security.state.unavailable", {}, locale));
    renderTopRiskBadge(null, elements.topRiskState, locale);
    setText(elements.researchStatus, t("security.unavailableUntilUniverse", {}, locale));
    elements.researchStatus.dataset.tone = "error";
  } else if (researchError) {
    setText(elements.securityState, t("security.state.unavailable", {}, locale));
    renderTopRiskBadge(null, elements.topRiskState, locale);
    setText(elements.researchStatus, errorText(researchError, locale));
  } else if (state.selectedTicker) {
    setText(elements.securityState, t("security.state.loading", {}, locale));
    renderTopRiskBadge(null, elements.topRiskState, locale);
    setText(elements.researchStatus, t("security.loading", { ticker: state.selectedTicker }, locale));
  }
}

function captureElements() {
  Object.assign(elements, {
    universeList: byId("universe-list"),
    universeCount: byId("universe-count"),
    universeStatus: byId("universe-status"),
    universeRetry: byId("universe-retry"),
    universeSearch: byId("universe-search"),
    sectorTaxonomy: byId("sector-taxonomy"),
    sectorKey: byId("sector-key"),
    sectorMembershipSummary: byId("sector-membership-summary"),
    sortKey: byId("sort-key"),
    sortDirection: byId("sort-direction"),
    marketDate: byId("market-date"),
    marketCoverage: byId("market-coverage"),
    selectedTicker: byId("selected-ticker"),
    selectedClose: byId("selected-close"),
    selectedChange: byId("selected-change"),
    observationDate: byId("observation-date"),
    securityState: byId("security-state"),
    securityRsState: byId("security-rs-state"),
    securityPoolState: byId("security-pool-state"),
    researchPoolToggle: byId("research-pool-toggle"),
    researchPoolActionStatus: byId("research-pool-action-status"),
    securityGateState: byId("security-gate-state"),
    marketRegimeGateState: byId("market-regime-gate-state"),
    topRiskState: byId("top-risk-state"),
    securityClassification: byId("security-classification"),
    researchStatus: byId("research-status"),
    stockRetry: byId("stock-retry"),
    dataWarnings: byId("data-warnings"),
    priceChart: byId("price-chart"),
    volumeChart: byId("volume-chart"),
    crosshairDetail: byId("crosshair-detail"),
    modelOutputContent: byId("model-output-content"),
    factorOverview: byId("factor-overview"),
    factorTableBody: byId("factor-table-body"),
    structureContent: byId("structure-content"),
    scenarioChart: byId("scenario-chart"),
    scenarioMeta: byId("scenario-meta"),
    updateData: byId("update-data"),
    updateStatus: byId("update-status"),
    cacheStatusSummary: byId("cache-status-summary"),
    cacheStatusDetails: byId("cache-status-details"),
    cacheStatusRefresh: byId("cache-status-refresh"),
    markerLayerCount: byId("marker-layer-count"),
    intradaySubscriptionSummary: byId("intraday-subscription-summary"),
    intradaySubscriptionList: byId("intraday-subscription-list"),
    selectedRealtimeToggle: byId("selected-realtime-toggle"),
    intradayLiveState: byId("intraday-live-state"),
    intradayLastTrade: byId("intraday-last-trade"),
    intradayBid: byId("intraday-bid"),
    intradayAsk: byId("intraday-ask"),
    intradaySpread: byId("intraday-spread"),
    intradayPriceChart: byId("intraday-price-chart"),
    intradayVolumeChart: byId("intraday-volume-chart"),
    intradayPressureBar: byId("intraday-pressure-bar"),
    intradayPressureDetail: byId("intraday-pressure-detail"),
    rangeControls: [...document.querySelectorAll("[data-range]")],
    forecastControls: [...document.querySelectorAll("[data-forecast-horizon]")],
    markerLayerControls: [...document.querySelectorAll("[data-marker-layer]")],
    markerPresetControls: [...document.querySelectorAll("[data-marker-preset]")],
    localeControls: [...document.querySelectorAll("[data-locale]")],
  });
}

export async function initializeDashboard() {
  captureElements();
  applyDocumentLocale(document, getLocale());
  unsubscribeLocale = subscribeLocale(applyLocale);
  researchPoolController = createResearchPoolControl({
    button: elements.researchPoolToggle,
    status: elements.researchPoolActionStatus,
    apiClient: api,
    locale: getLocale(),
    onChanged: refreshUniverseAfterResearchPoolChange,
  });
  if (elements.selectedRealtimeToggle && elements.intradaySubscriptionList) {
    intradayLiveController = createIntradayLiveController({
      api,
      locale: () => store.getState().locale,
      elements: {
        summary: elements.intradaySubscriptionSummary,
        list: elements.intradaySubscriptionList,
        toggle: elements.selectedRealtimeToggle,
        state: elements.intradayLiveState,
        lastTrade: elements.intradayLastTrade,
        bid: elements.intradayBid,
        ask: elements.intradayAsk,
        spread: elements.intradaySpread,
        priceChart: elements.intradayPriceChart,
        volumeChart: elements.intradayVolumeChart,
        pressureBar: elements.intradayPressureBar,
        pressureDetail: elements.intradayPressureDetail,
      },
    });
  }
  chartController = createLinkedCharts(
    elements.priceChart,
    elements.volumeChart,
    elements.crosshairDetail,
    {
      locale: getLocale(),
      markerLayers: selectedMarkerLayers,
      modelOutputEl: elements.modelOutputContent,
      onForecastDate: async (date) => {
        const requestGeneration = stockRequestSequence;
        const ticker = store.getState().selectedTicker;
        if (!ticker) return null;
        const payload = await api.getStockForecast(ticker, date);
        return (
          stockRequestSequence === requestGeneration
          && store.getState().selectedTicker === ticker
        ) ? payload : null;
      },
    },
  );
  syncMarkerLayerControls(selectedMarkerLayers);
  updateController = createUpdateController({
    button: elements.updateData,
    status: elements.updateStatus,
    onTerminal: refreshUniverseAfterUpdate,
  });
  cacheStatusController = createCacheStatusController({
    summary: elements.cacheStatusSummary,
    details: elements.cacheStatusDetails,
    refreshButton: elements.cacheStatusRefresh,
  });
  bindControls();
  await updateController.initialize();
  await cacheStatusController.initialize();
  await intradayLiveController?.initialize();
  await loadUniverse();
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initializeDashboard, { once: true });
  window.addEventListener("pagehide", () => {
    chartController?.destroy();
    updateController?.destroy();
    cacheStatusController?.destroy();
    intradayLiveController?.destroy();
    unsubscribeLocale?.();
  }, { once: true });
}
