import { api } from "./api.js";
import { createLinkedCharts } from "./charts.js";
import { renderFactors, renderStructures } from "./factors.js";
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

function formatPercent(value) {
  if (!Number.isFinite(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function describeError(error) {
  if (error && typeof error.message === "string") return error.message;
  return "The local dashboard could not complete the request";
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
  });
  setText(elements.universeCount, `${rows.length}/${state.universe.length}`);
  setText(
    elements.universeStatus,
    state.universe.length ? `${rows.length} ticker${rows.length === 1 ? "" : "s"} shown` : "No local tickers available",
  );
  elements.universeStatus.removeAttribute("data-tone");
}

function renderWarnings(warnings) {
  elements.dataWarnings.replaceChildren();
  const fragment = document.createDocumentFragment();
  warnings.forEach((warning) => {
    const item = document.createElement("li");
    item.textContent = String(warning).replaceAll("_", " ");
    fragment.append(item);
  });
  elements.dataWarnings.append(fragment);
}

function renderStockHeader(payload) {
  const summary = payload.summary || {};
  setText(elements.selectedTicker, payload.ticker);
  setText(elements.selectedClose, formatNumber(summary.close));
  setText(elements.selectedChange, formatPercent(summary.daily_return));
  setText(elements.observationDate, payload.observation_date);
  setText(elements.securityState, summary.inactive ? "Inactive" : "Active");
  setText(elements.researchStatus, `Loaded observations through ${payload.observation_date || "an unknown date"}.`);
  renderWarnings(Array.isArray(payload.warnings) ? payload.warnings : []);
}

function clearResearchPanels() {
  chartController.setChartData({ chart: [] });
  renderFactors([], {
    overview: elements.factorOverview,
    tableBody: elements.factorTableBody,
  });
  renderStructures(null, elements.structureContent);
  renderScenarios(null, {
    chart: elements.scenarioChart,
    metadata: elements.scenarioMeta,
  });
}

async function selectTicker(ticker) {
  if (!ticker) return;
  const requestSequence = ++stockRequestSequence;
  store.setState({ selectedTicker: ticker, stockPayload: null });
  persistSelectedTicker(ticker);
  paintUniverse();
  setText(elements.selectedTicker, ticker);
  setText(elements.securityState, "Loading");
  setText(elements.researchStatus, `Loading ${ticker} from the local database…`);
  elements.researchStatus.removeAttribute("data-tone");
  renderWarnings([]);
  clearResearchPanels();

  try {
    const payload = await api.getStock(ticker);
    if (requestSequence !== stockRequestSequence) return;
    store.setState({ stockPayload: payload });
    renderStockHeader(payload);
    chartController.setChartData(payload);
    renderFactors(payload.factors, {
      overview: elements.factorOverview,
      tableBody: elements.factorTableBody,
    });
    renderStructures(payload.structures, elements.structureContent);
    renderScenarios(payload.scenarios, {
      chart: elements.scenarioChart,
      metadata: elements.scenarioMeta,
    });
  } catch (error) {
    if (requestSequence !== stockRequestSequence) return;
    setText(elements.securityState, "Unavailable");
    setText(elements.researchStatus, describeError(error));
    elements.researchStatus.dataset.tone = "error";
  }
}

function coverageText(payload) {
  const buckets = payload && payload.freshness && payload.freshness.by_date;
  const current = Array.isArray(buckets) && buckets.length ? buckets[0].tickers : 0;
  const total = Array.isArray(payload.tickers) ? payload.tickers.length : 0;
  return `${current}/${total} current`;
}

async function loadUniverse() {
  setText(elements.universeStatus, "Loading local universe…");
  try {
    const payload = await api.getUniverse();
    const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
    const selectedTicker = chooseInitialTicker(rows, readStoredTicker());
    store.setState({ universePayload: payload, universe: rows, selectedTicker });
    setText(elements.marketDate, payload.asof || "No data");
    setText(elements.marketCoverage, coverageText(payload));
    paintUniverse();
    if (selectedTicker) await selectTicker(selectedTicker);
  } catch (error) {
    store.setState({ universe: [], universePayload: null, selectedTicker: null });
    paintUniverse();
    setText(elements.universeStatus, describeError(error));
    elements.universeStatus.dataset.tone = "error";
    setText(elements.researchStatus, "Stock research is unavailable until the local universe loads.");
  }
}

async function refreshUniverseAfterUpdate() {
  const previous = store.getState();
  const previousTicker = previous.selectedTicker;
  const previousObservationDate = previous.stockPayload?.observation_date || null;
  try {
    const payload = await api.getUniverse();
    const rows = Array.isArray(payload.tickers) ? payload.tickers : [];
    const selectedTicker = chooseInitialTicker(rows, previousTicker);
    store.setState({ universePayload: payload, universe: rows, selectedTicker });
    setText(elements.marketDate, payload.asof || "No data");
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
    setText(elements.universeStatus, describeError(error));
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
    setText(elements.sortDirection, direction === "asc" ? "Ascending" : "Descending");
    elements.sortDirection.setAttribute("aria-label", `Sort ${direction === "asc" ? "ascending" : "descending"}`);
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
  });
}

export async function initializeDashboard() {
  captureElements();
  chartController = createLinkedCharts(
    elements.priceChart,
    elements.volumeChart,
    elements.crosshairDetail,
  );
  updateController = createUpdateController({
    button: elements.updateData,
    status: elements.updateStatus,
    onTerminal: refreshUniverseAfterUpdate,
  });
  bindControls();
  await loadUniverse();
}

if (typeof document !== "undefined") {
  document.addEventListener("DOMContentLoaded", initializeDashboard, { once: true });
  window.addEventListener("pagehide", () => {
    chartController?.destroy();
    updateController?.destroy();
  }, { once: true });
}
