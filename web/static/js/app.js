import { api } from "./api.js";
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
import { renderScenarios } from "./scenarios.js";
import {
  chooseInitialTicker,
  persistSelectedTicker,
  readStoredTicker,
  store,
} from "./store.js";
import { filterTickers, renderUniverse, sortTickers } from "./universe.js";
import { createUpdateController, shouldReloadSelectedTicker } from "./update.js";

const elements = {};
let stockRequestSequence = 0;
let chartController = null;
let updateController = null;
let unsubscribeLocale = null;
let universeError = null;
let researchError = null;

function byId(id) {
  return document.getElementById(id);
}

function setText(element, value) {
  element.textContent = value == null || value === "" ? "—" : String(value);
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

function paintUniverse() {
  const state = store.getState();
  const rows = currentRows();
  renderUniverse(elements.universeList, rows, {
    selectedTicker: state.selectedTicker,
    onSelect: selectTicker,
    locale: state.locale,
  });
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
  researchError = null;
  store.setState({ selectedTicker: ticker, stockPayload: null });
  persistSelectedTicker(ticker);
  paintUniverse();
  setText(elements.selectedTicker, ticker);
  const locale = store.getState().locale;
  setText(elements.securityState, t("security.state.loading", {}, locale));
  clearStockQuote(elements);
  setText(elements.researchStatus, t("security.loading", { ticker }, locale));
  elements.researchStatus.removeAttribute("data-tone");
  renderWarnings([]);
  clearResearchPanels();

  try {
    const payload = await api.getStock(ticker);
    if (requestSequence !== stockRequestSequence) return;
    store.setState({ stockPayload: payload });
    researchError = null;
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
    setText(elements.researchStatus, errorText(researchError, store.getState().locale));
    elements.researchStatus.dataset.tone = "error";
  }
}

function coverageText(payload) {
  const buckets = payload && payload.freshness && payload.freshness.by_date;
  const current = Array.isArray(buckets) && buckets.length ? buckets[0].tickers : 0;
  const total = Array.isArray(payload.tickers) ? payload.tickers.length : 0;
  return t("header.coverageValue", { current, total }, store.getState().locale);
}

async function loadUniverse() {
  setText(elements.universeStatus, t("universe.loading", {}, store.getState().locale));
  try {
    const payload = await api.getUniverse();
    universeError = null;
    const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
    const selectedTicker = chooseInitialTicker(rows, readStoredTicker());
    store.setState({ universePayload: payload, universe: rows, selectedTicker });
    setText(elements.marketDate, payload.asof || t("header.noData", {}, store.getState().locale));
    setText(elements.marketCoverage, coverageText(payload));
    paintUniverse();
    if (selectedTicker) await selectTicker(selectedTicker);
  } catch (error) {
    universeError = errorState(error);
    store.setState({ universe: [], universePayload: null, selectedTicker: null });
    paintUniverse();
    elements.universeStatus.dataset.tone = "error";
    setText(
      elements.securityState,
      t("security.state.unavailable", {}, store.getState().locale),
    );
    setText(
      elements.researchStatus,
      t("security.unavailableUntilUniverse", {}, store.getState().locale),
    );
    elements.researchStatus.dataset.tone = "error";
  }
}

async function refreshUniverseAfterUpdate() {
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
    if (selectedTicker && (selectionChanged || observationChanged)) {
      await selectTicker(selectedTicker);
    }
  } catch (error) {
    universeError = errorState(error);
    setText(elements.universeStatus, errorText(universeError, store.getState().locale));
    elements.universeStatus.dataset.tone = "error";
  }
}

function bindControls() {
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
  chartController?.setLocale(locale);
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
    setText(elements.researchStatus, t("security.unavailableUntilUniverse", {}, locale));
    elements.researchStatus.dataset.tone = "error";
  } else if (researchError) {
    setText(elements.securityState, t("security.state.unavailable", {}, locale));
    setText(elements.researchStatus, errorText(researchError, locale));
  } else if (state.selectedTicker) {
    setText(elements.securityState, t("security.state.loading", {}, locale));
    setText(elements.researchStatus, t("security.loading", { ticker: state.selectedTicker }, locale));
  }
}

function captureElements() {
  Object.assign(elements, {
    universeList: byId("universe-list"),
    universeCount: byId("universe-count"),
    universeStatus: byId("universe-status"),
    universeSearch: byId("universe-search"),
    sortKey: byId("sort-key"),
    sortDirection: byId("sort-direction"),
    marketDate: byId("market-date"),
    marketCoverage: byId("market-coverage"),
    selectedTicker: byId("selected-ticker"),
    selectedClose: byId("selected-close"),
    selectedChange: byId("selected-change"),
    observationDate: byId("observation-date"),
    securityState: byId("security-state"),
    researchStatus: byId("research-status"),
    dataWarnings: byId("data-warnings"),
    priceChart: byId("price-chart"),
    volumeChart: byId("volume-chart"),
    crosshairDetail: byId("crosshair-detail"),
    factorOverview: byId("factor-overview"),
    factorTableBody: byId("factor-table-body"),
    structureContent: byId("structure-content"),
    scenarioChart: byId("scenario-chart"),
    scenarioMeta: byId("scenario-meta"),
    updateData: byId("update-data"),
    updateStatus: byId("update-status"),
    rangeControls: [...document.querySelectorAll("[data-range]")],
    localeControls: [...document.querySelectorAll("[data-locale]")],
  });
}

export async function initializeDashboard() {
  captureElements();
  applyDocumentLocale(document, getLocale());
  unsubscribeLocale = subscribeLocale(applyLocale);
  chartController = createLinkedCharts(
    elements.priceChart,
    elements.volumeChart,
    elements.crosshairDetail,
    { locale: getLocale() },
  );
  updateController = createUpdateController({
    button: elements.updateData,
    status: elements.updateStatus,
    onTerminal: refreshUniverseAfterUpdate,
  });
  bindControls();
  await updateController.initialize();
  await loadUniverse();
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initializeDashboard, { once: true });
  window.addEventListener("pagehide", () => {
    chartController?.destroy();
    updateController?.destroy();
    unsubscribeLocale?.();
  }, { once: true });
}
