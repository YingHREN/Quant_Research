import assert from "node:assert/strict";

const [appUri, mode = "success"] = process.argv.slice(2);

class Element {
  constructor(tagName = "div", id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.dataset = {};
    this.attributes = {};
    this.children = [];
    this.listeners = new Map();
    this.className = "";
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.value = "";
    this.clientWidth = 800;
    this.clientHeight = 240;
    this.style = { setProperty(name, value) { this[name] = value; } };
    this._textContent = "";
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this._textContent = value == null ? "" : String(value);
    this.children = [];
  }

  append(...items) {
    this.children.push(...items);
  }

  replaceChildren(...items) {
    this._textContent = "";
    this.children = [...items];
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name];
  }

  removeAttribute(name) {
    delete this.attributes[name];
    if (name.startsWith("data-")) {
      const key = name.slice(5).replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
      delete this.dataset[key];
    }
  }

  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(handler);
  }

  removeEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    this.listeners.set(name, handlers.filter((candidate) => candidate !== handler));
  }

  dispatch(name) {
    for (const handler of this.listeners.get(name) || []) {
      handler({ currentTarget: this, target: this });
    }
  }
}

function textTree(node) {
  return [node.textContent, ...node.children.map(textTree)].join(" ").replace(/\s+/g, " ").trim();
}

function descendants(node) {
  return node.children.flatMap((child) => [child, ...descendants(child)]);
}

function byClass(node, className) {
  return descendants(node).filter((child) => child.className === className);
}

const ids = [
  "universe-list", "universe-count", "universe-status", "universe-retry",
  "universe-search", "sort-key",
  "sort-direction", "sector-taxonomy", "sector-key", "sector-membership-summary",
  "market-date", "market-coverage", "selected-ticker", "selected-close",
  "selected-change", "observation-date", "security-state", "research-status", "stock-retry",
  "data-warnings",
  "top-risk-state", "security-classification",
  "price-chart", "volume-chart", "crosshair-detail", "model-output-content",
  "factor-overview", "factor-table-body",
  "structure-content", "scenario-chart", "scenario-meta", "update-data", "update-status",
  "marker-layer-count",
];
const activeIds = mode === "missing-top-risk-element"
  ? ids.filter((id) => id !== "top-risk-state")
  : ids;
const elements = new Map(activeIds.map((id) => [id, new Element("div", id)]));
for (const id of ["universe-retry", "stock-retry"]) {
  if (elements.has(id)) elements.get(id).hidden = true;
}
elements.get("price-chart").clientHeight = 400;
elements.get("volume-chart").clientHeight = 180;
const body = new Element("body");

const zhButton = new Element("button");
zhButton.dataset.locale = "zh-CN";
zhButton.dataset.i18n = "locale.zh";
const enButton = new Element("button");
enButton.dataset.locale = "en";
enButton.dataset.i18n = "locale.en";
const rangeButton = new Element("button");
rangeButton.dataset.range = "1y";
rangeButton.dataset.i18n = "chart.range.1y";
const markerLayerKeys = [
  "strict_vcp", "vcp_breakout", "pocket_pivot", "tight_platform",
  "structure_reversal", "early_reversal", "prior_high_breakout",
  "trendline_breakout", "higher_low", "top_risk",
];
const markerLayerControls = markerLayerKeys.map((key) => {
  const control = new Element("input");
  control.dataset.markerLayer = key;
  control.checked = ["strict_vcp", "vcp_breakout", "pocket_pivot"].includes(key);
  return control;
});
const markerPresetControls = ["core", "all", "none"].map((preset) => {
  const control = new Element("button");
  control.dataset.markerPreset = preset;
  control.dataset.i18n = `chart.layers.preset.${preset}`;
  return control;
});
const staticNodes = [zhButton, enButton, rangeButton, ...markerPresetControls];

const documentListeners = new Map();
globalThis.document = {
  body,
  documentElement: { lang: "zh-CN" },
  createElement(tagName) { return new Element(tagName); },
  createDocumentFragment() { return new Element("fragment"); },
  getElementById(id) { return elements.get(id) || null; },
  querySelectorAll(selector) {
    if (selector === "[data-locale]") return [zhButton, enButton];
    if (selector === "[data-range]") return [rangeButton];
    if (selector === "[data-filter]") return [];
    if (selector === "[data-marker-layer]") return markerLayerControls;
    if (selector === "[data-marker-preset]") return markerPresetControls;
    if (selector === "[data-i18n]") return staticNodes;
    if (selector === "[data-i18n-placeholder]" || selector === "[data-i18n-aria-label]") return [];
    return [];
  },
  addEventListener(name, handler) { documentListeners.set(name, handler); },
};
globalThis.window = { addEventListener() {}, removeEventListener() {} };
const storageValues = new Map();
globalThis.localStorage = {
  getItem(key) { return storageValues.get(key) ?? null; },
  setItem(key, value) { storageValues.set(key, value); },
};

const charts = [];
const markerControllers = [];
function createChart(element, options) {
  const scale = {
    subscribeVisibleLogicalRangeChange() {}, unsubscribeVisibleLogicalRangeChange() {},
    setVisibleLogicalRange() {}, fitContent() {},
  };
  const chart = {
    element, options, series: [], priceLines: [], removed: false,
    timeScale() { return scale; },
    priceScale() { return { applyOptions() {} }; },
    addSeries(type, seriesOptions) {
      const series = {
        type, options: { ...seriesOptions }, data: [],
        setData(data) { this.data = data; },
        applyOptions(next) { Object.assign(this.options, next); },
        createPriceLine(line) { chart.priceLines.push(line); return line; },
        removePriceLine(line) {
          const index = chart.priceLines.indexOf(line);
          if (index >= 0) chart.priceLines.splice(index, 1);
        },
      };
      chart.series.push(series);
      return series;
    },
    subscribeCrosshairMove(handler) { this.crosshairHandler = handler; },
    unsubscribeCrosshairMove() {},
    subscribeClick(handler) { this.clickHandler = handler; }, unsubscribeClick() {},
    setCrosshairPosition() {}, clearCrosshairPosition() {}, applyOptions(next) {
      this.options = { ...this.options, ...next };
    },
    remove() { this.removed = true; },
  };
  charts.push(chart);
  return chart;
}
globalThis.LightweightCharts = {
  CandlestickSeries: "candles", HistogramSeries: "histogram", LineSeries: "line",
  BaselineSeries: "baseline",
  CrosshairMode: { Normal: 0 }, LineStyle: { Dashed: 2 },
  createChart,
  createSeriesMarkers(_series, markers) {
    const controller = { markers, setMarkers(next) { this.markers = next; } };
    markerControllers.push(controller);
    return controller;
  },
};

const universe = {
  asof: "2026-07-22",
  freshness: { by_date: [{ date: "2026-07-22", tickers: 1 }] },
  tickers: [{
    ticker: "AAA", latest_date: "2026-07-22", lag_days: 0, inactive: false,
    stale: false, shape_state: "strict_vcp", momentum_percentile: 80,
    sector_classification: {
      state: "agree",
      sec: {
        sector_key: "technology", confidence: 1, source: "sec",
        rule_version: "sec_sic_v1", asof: "2026-07-24",
      },
      market_behavior: {
        sector_key: "technology", benchmark_ticker: "XLK", confidence: 0.8,
        source: "price_returns", rule_version: "market_behavior_v1",
        asof: "2026-07-24", residual_correlation: 0.42,
        residual_beta: 1.2, relative_return_63d: 0.08, common_days: 252,
      },
    },
  }],
  classification_summary: {
    status: "available", asof: "2026-07-24", research_universe_count: 1014,
    sector_counts: {
      sec: { technology: 237 },
      market_behavior: { technology: 154 },
    },
  },
  factor_groups: [{
    key: "trend", label: "Trend", methodology: "Moving-average position diagnostics.", overview: true,
  }],
};
const row = {
  time: "2026-07-22", open: 99, high: 102, low: 98, close: 101, volume: 1200,
  volume_ma20: 1000, volume_ratio: 1.2, volume_ratio_change: 0.15, ema20: 100,
  sma50: 95, sma200: 90, daily_return: 0.01, true_range_pct: 4, volume_change: 0.1,
  atr20: 3, pivot: 100, pivot_distance_pct: 1, pivot_distance_change_pct: 0.75,
  ema20_cross: "above", sma50_cross: null,
};
const stock = {
  ticker: "AAA", observation_date: "2026-07-22",
  summary: { close: 101, daily_return: 0.01, daily_return_unit: "fraction", stale: false, inactive: false },
  top_risk: {
    model_key: "high_level_distribution_risk_v1",
    model_version: "v1",
    status: "available",
    unavailable_reason: null,
    latest: {
      time: "2026-07-22", score: 72, raw_score: 72,
      state: "confirmed", raw_state: "confirmed", memory_age_sessions: 0,
    },
    events: [],
  },
  warnings: [], chart: [row],
  forecasts: {
    model: { key: "ridge_direction_v1", version: "v3" },
    date_coverage: { computed_dates: [row.time] },
    by_date: {
      [row.time]: {
        "20": {
          asof_date: row.time,
          target_date: "2026-08-19",
          horizon_sessions: 20,
          direction: "down",
          raw_direction: "up",
          predicted_return: -0.02,
          model_outputs: {
            primary: [{
              key: "ridge_direction_v1", version: "v3",
              kind: "statistical_forecast", lifecycle: "production",
              status: "available", timing: "next_session_open",
              name_key: "model.ridge.name",
              explanation_key: "model.ridge.explanation",
              limitation_key: "model.ridge.limitation",
              predicted_return: 0.04, direction: "up",
            }],
            downside: [{
              key: "bearish_turn_immediate_v1", version: "v1",
              kind: "rule_score", lifecycle: "production",
              status: "active", timing: "close_confirmed",
              name_key: "model.immediateRisk.name",
              explanation_key: "model.immediateRisk.explanation",
              limitation_key: "model.immediateRisk.limitation",
              score: 80, threshold: 70, conditions: ["distribution_volume"],
            }],
            bullish_structure: [{
              key: "bullish_structure_reversal_v1", version: "v1",
              kind: "rule_score", lifecycle: "production",
              status: "inactive", timing: "close_confirmed",
              name_key: "model.structuralReversal.name",
              explanation_key: "model.structuralReversal.explanation",
              limitation_key: "model.structuralReversal.limitation",
              score: 1, maximum_score: 3,
            }],
            decision: {
              key: "forecast_decision_policy", version: "v2",
              kind: "decision_policy", lifecycle: "production",
              status: "available", timing: "next_session_open",
              name_key: "model.decisionPolicy.name",
              explanation_key: "model.decisionPolicy.explanation",
              limitation_key: "model.decisionPolicy.limitation",
              final_direction: "down", action: "override_to_down",
              reasons: ["immediate_bearish_confirmation"],
            },
          },
        },
      },
    },
  },
  structures: {
    strict_vcp: {
      reject_reason: "历史不足", rejection_reason_code: "insufficient_history",
      contractions: [], n_contractions: 0,
    },
    tight_platform: {
      is_platform: false, reason: "历史不足",
      rejection_reason_code: "insufficient_history",
    },
    key_levels: { strict_vcp_pivot: 103 },
    annotations: [
      { time: row.time, type: "strict_vcp", label: "Strict VCP" },
      { time: row.time, type: "top_risk_watch", label: "Top downside risk watch" },
      { time: row.time, type: "top_risk_high", label: "Top downside risk high" },
      { time: row.time, type: "top_risk_confirmed", label: "Top downside risk confirmed" },
      { time: row.time, type: "top_risk_recovery", label: "Top downside risk cleared" },
    ],
  },
  factors: [{
    key: "close_vs_ema20_pct", label: "Close vs EMA20", group: "trend", overview: true,
    raw_value: 1, formatted: "1.00%", percentile: 0.75, peer_count: 8, display_score: 75,
    observation_date: "2026-07-22", missing: false, missing_reason: null,
    description: "Close relative to the point-in-time 20-session EMA.",
    methodology: "Close divided by the 20-session exponential moving average, minus one, expressed in percent.",
    version: "builtin-v1",
  }, {
    key: "strict_vcp", label: "Strict VCP", group: "structure", overview: true,
    raw_value: {
      reject_reason: "历史不足", rejection_reason_code: "insufficient_history",
      contractions: [], n_contractions: 0,
    },
    formatted: "Rejected: 历史不足", percentile: null, peer_count: null,
    display_score: null, observation_date: "2026-07-22", missing: false,
    missing_reason: null, description: "Precision-first VCP diagnostic, including its rejection reason.",
    methodology: "Canonical strict VCP gates evaluate trend, base depth, contraction legs, volume dry-up, and extension.",
    window: "Up to 250 sessions; candidate bases span 20 to 80 sessions",
    direction: "neutral", version: "builtin-v1",
  }],
  scenarios: {
    provider: "historical_distribution", observation_date: "2026-07-22",
    methodology: "Descriptive historical scenarios from non-overlapping horizon returns available at the observation date; not predictions or probabilities.",
    horizons: {
      "20": {
        available: true, horizon_sessions: 20, sample_count: 12, non_overlapping: true,
        methodology: "12 non-overlapping 20-session historical returns, with absolute quantiles capped at three times current 63-session realized-volatility scaling.",
        paths: {
          pessimistic: [{ session: 0, price: 101 }, { session: 20, price: 90 }],
          median: [{ session: 0, price: 101 }, { session: 20, price: 103 }],
          optimistic: [{ session: 0, price: 101 }, { session: 20, price: 111 }],
        },
      },
    },
  },
};
if (mode === "top-risk-fading") {
  stock.top_risk.latest = {
    ...stock.top_risk.latest,
    score: 48,
    raw_score: 0,
    state: "fading",
    raw_state: "inactive",
    memory_age_sessions: 3,
  };
}
if (mode === "top-risk-unavailable") {
  stock.top_risk = {
    model_key: "high_level_distribution_risk_v1",
    model_version: "v1",
    status: "unavailable",
    unavailable_reason: "not_available",
    latest: null,
    events: [],
  };
}

function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, async json() { return payload; } };
}

const nativeSetTimeout = globalThis.setTimeout;
globalThis.setTimeout = (callback) => nativeSetTimeout(callback, 0);
let universeAttempts = 0;
let stockAttempts = 0;

globalThis.fetch = async (path) => {
  if (path === "/api/update/status") return jsonResponse({ state: "idle" });
  if (path === "/api/universe") {
    universeAttempts += 1;
    if (
      mode === "universe-error"
      || (mode === "universe-error-then-retry" && universeAttempts <= 3)
    ) {
      return jsonResponse({ error: { code: "market_data_unavailable", message: "Market data is unavailable" } }, 503);
    }
    return jsonResponse(universe);
  }
  if (path === "/api/stocks/AAA") {
    stockAttempts += 1;
    if (
      mode === "stock-error"
      || (mode === "stock-error-then-retry" && stockAttempts <= 3)
    ) {
      return jsonResponse({ error: { code: "internal_error", message: "An internal error occurred" } }, 500);
    }
    if (mode === "stock-unknown-error") {
      return jsonResponse({
        error: { code: "future_error", message: "unsafe /Users/alice/private.db detail" },
      }, 500);
    }
    return jsonResponse(stock);
  }
  throw new Error(`Unexpected fetch: ${path}`);
};

const app = await import(appUri);
await app.initializeDashboard();

if (mode === "success") {
  const priceChart = charts[0];
  const volumeChart = charts[1];
  priceChart.clickHandler({ time: row.time });
  const factorZh = textTree(elements.get("factor-overview"));
  const tableZh = textTree(elements.get("factor-table-body"));
  const scenarioZh = textTree(elements.get("scenario-meta"));
  const structureZh = textTree(elements.get("structure-content"));
  const chartZh = textTree(elements.get("crosshair-detail"));
  const modelZh = textTree(elements.get("model-output-content"));
  const seriesDataBeforeLocale = priceChart.series.map((series) => JSON.stringify(series.data));
  priceChart.crosshairHandler({ time: null });
  const lockedModelZh = textTree(elements.get("model-output-content"));
  const priceLinesZh = priceChart.priceLines.map((line) => line.title);
  const volumeTitlesZh = volumeChart.series.map((series) => series.options.title).filter(Boolean);
  const markersZh = markerControllers[0].markers.map((marker) => marker.text);
  const topRiskZh = elements.get("top-risk-state").textContent;
  const topRiskToneZh = elements.get("top-risk-state").dataset.tone;
  const meterZh = byClass(elements.get("factor-overview"), "factor-bar-track")[0];
  const strictInfoZh = byClass(elements.get("factor-table-body"), "factor-info").at(-1);
  strictInfoZh.dispatch("pointerenter");
  const popoverZh = textTree(body);
  const datesZh = [priceChart, volumeChart].map((chart) => [
    chart.options.timeScale.tickMarkFormatter("2026-07-17"),
    chart.options.localization.timeFormatter("2026-07-17"),
  ]);

  enButton.dispatch("click");

  const factorEn = textTree(elements.get("factor-overview"));
  const tableEn = textTree(elements.get("factor-table-body"));
  const scenarioEn = textTree(elements.get("scenario-meta"));
  const structureEn = textTree(elements.get("structure-content"));
  const chartEn = textTree(elements.get("crosshair-detail"));
  const modelEn = textTree(elements.get("model-output-content"));
  const seriesDataAfterLocale = priceChart.series.map((series) => JSON.stringify(series.data));
  const priceLinesEn = priceChart.priceLines.map((line) => line.title);
  const volumeTitlesEn = volumeChart.series.map((series) => series.options.title).filter(Boolean);
  const markersEn = markerControllers[0].markers.map((marker) => marker.text);
  const topRiskEn = elements.get("top-risk-state").textContent;
  const topRiskToneEn = elements.get("top-risk-state").dataset.tone;
  const datesEn = [priceChart, volumeChart].map((chart) => [
    chart.options.timeScale.tickMarkFormatter("2026-07-17"),
    chart.options.localization.timeFormatter("2026-07-17"),
  ]);
  const meterEn = byClass(elements.get("factor-overview"), "factor-bar-track")[0];
  const strictInfoEn = byClass(elements.get("factor-table-body"), "factor-info").at(-1);
  strictInfoEn.dispatch("pointerenter");
  const popoverEn = textTree(body);

  assert.match(factorZh, /趋势/);
  assert.match(tableZh, /收盘价相对 EMA20/);
  assert.match(tableZh, /已拒绝：历史数据不足/);
  assert.match(popoverZh, /当前值 已拒绝：历史数据不足/);
  assert.match(tableZh, /第 75 百分位 · 8 个同日样本/);
  assert.match(tableZh, /收盘价相对时点一致的 20 日 EMA/);
  assert.match(scenarioZh, /基于观察日可用的非重叠周期收益/);
  assert.match(structureZh, /关键价位/);
  assert.match(structureZh, /向上突破准备形态（严格 VCP）枢轴点/);
  assert.match(structureZh, /拒绝原因 历史数据不足/);
  assert.match(chartZh, /开盘价/);
  assert.match(chartZh, /向上交叉/);
  assert.match(chartZh, /已锁定/);
  assert.match(modelZh, /Ridge 收益率预测/);
  assert.match(modelZh, /最终方向 下跌/);
  assert.match(modelZh, /规则分数，不是概率/);
  assert.match(lockedModelZh, /2026-07-22/);
  assert.deepEqual(seriesDataAfterLocale, seriesDataBeforeLocale);
  assert.deepEqual(priceLinesZh, ["向上突破准备形态（严格 VCP）枢轴点"]);
  assert.deepEqual(markersZh, ["向上突破准备形态（严格 VCP）", "预测起点 · 下跌"]);
  assert.equal(topRiskZh, "顶部风险 72 · 已确认");
  assert.equal(topRiskToneZh, "confirmed");
  assert.ok(volumeTitlesZh.includes("成交量 MA20"));
  assert.equal(meterZh.getAttribute("aria-label"), "收盘价相对 EMA20 展示分数");
  assert.deepEqual(datesZh, [["07-17", "2026-07-17"], ["07-17", "2026-07-17"]]);

  assert.equal(document.documentElement.lang, "en");
  assert.equal(enButton.getAttribute("aria-pressed"), "true");
  assert.match(factorEn, /Trend/);
  assert.match(tableEn, /75th percentile · 8 same-date peers/);
  assert.match(tableEn, /Rejected: Insufficient history/);
  assert.doesNotMatch(tableEn, /历史不足/);
  assert.match(popoverEn, /Current value Rejected: Insufficient history/);
  assert.doesNotMatch(popoverEn, /历史不足/);
  assert.match(scenarioEn, /Descriptive historical scenarios/);
  assert.match(structureEn, /Key Levels/);
  assert.match(structureEn, /Bullish Breakout Setup \(Strict VCP\) Pivot/);
  assert.match(structureEn, /Reject Reason Insufficient history/);
  assert.doesNotMatch(structureEn, /历史不足/);
  assert.match(chartEn, /Open/);
  assert.match(chartEn, /Crossed above/);
  assert.match(chartEn, /Locked/);
  assert.match(modelEn, /Ridge return forecast/);
  assert.match(modelEn, /Final direction Down/);
  assert.match(modelEn, /Rule score, not a probability/);
  assert.deepEqual(
    priceLinesEn,
    ["Bullish breakout setup (Strict VCP) pivot"],
  );
  assert.deepEqual(
    markersEn,
    ["Bullish breakout setup (Strict VCP)", "Forecast start · Down"],
  );
  assert.equal(topRiskEn, "Top risk 72 · Confirmed");
  assert.equal(topRiskToneEn, "confirmed");
  assert.ok(volumeTitlesEn.includes("Volume MA20"));
  assert.equal(meterEn.getAttribute("aria-label"), "Close vs EMA20 display score");
  assert.deepEqual(datesEn, datesZh);
  console.log(JSON.stringify({ factorZh, tableZh, scenarioZh, structureZh, chartZh,
    modelZh, lockedModelZh, popoverZh, factorEn, tableEn, scenarioEn, structureEn,
    chartEn, modelEn, popoverEn, topRiskZh, topRiskEn }));
} else if (mode === "missing-top-risk-element") {
  assert.equal(elements.get("universe-count").textContent, "1/1");
  assert.match(elements.get("research-status").textContent, /2026-07-22/);
  assert.equal(elements.get("selected-ticker").textContent, "AAA");
  console.log(JSON.stringify({
    count: elements.get("universe-count").textContent,
    ticker: elements.get("selected-ticker").textContent,
  }));
} else if (mode === "top-risk-fading" || mode === "top-risk-unavailable") {
  const zh = {
    text: elements.get("top-risk-state").textContent,
    tone: elements.get("top-risk-state").dataset.tone,
  };
  enButton.dispatch("click");
  const en = {
    text: elements.get("top-risk-state").textContent,
    tone: elements.get("top-risk-state").dataset.tone,
  };
  console.log(JSON.stringify({ zh, en }));
} else if (mode === "universe-error") {
  const zh = {
    universe: elements.get("universe-status").textContent,
    universeTone: elements.get("universe-status").dataset.tone,
    research: elements.get("research-status").textContent,
    researchTone: elements.get("research-status").dataset.tone,
    security: elements.get("security-state").textContent,
  };
  enButton.dispatch("click");
  const en = {
    universe: elements.get("universe-status").textContent,
    universeTone: elements.get("universe-status").dataset.tone,
    research: elements.get("research-status").textContent,
    researchTone: elements.get("research-status").dataset.tone,
    security: elements.get("security-state").textContent,
  };
  assert.deepEqual(zh, {
    universe: "市场数据不可用。", universeTone: "error",
    research: "本地股票池加载完成前无法查看股票研究。", researchTone: "error",
    security: "不可用",
  });
  assert.deepEqual(en, {
    universe: "Market data is unavailable.", universeTone: "error",
    research: "Stock research is unavailable until the local universe loads.", researchTone: "error",
    security: "Unavailable",
  });
  console.log(JSON.stringify({ zh, en }));
} else if (mode === "stock-error" || mode === "stock-unknown-error") {
  const zh = elements.get("research-status").textContent;
  enButton.dispatch("click");
  const en = elements.get("research-status").textContent;
  if (mode === "stock-error") {
    assert.equal(zh, "本地仪表板遇到内部错误。");
    assert.equal(en, "The local dashboard encountered an internal error.");
    assert.notEqual(zh, "An internal error occurred");
  } else {
    assert.equal(zh, "本地仪表板无法完成请求");
    assert.equal(en, "The local dashboard could not complete the request");
    assert.ok(!zh.includes("/Users/") && !en.includes("/Users/"));
  }
  assert.equal(elements.get("research-status").dataset.tone, "error");
  console.log(JSON.stringify({ zh, en }));
} else if (mode === "universe-error-then-retry") {
  assert.equal(elements.get("universe-count").textContent, "0/0");
  assert.equal(elements.get("universe-retry").hidden, false);
  elements.get("universe-retry").dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.get("universe-count").textContent, "1/1");
  assert.equal(elements.get("selected-ticker").textContent, "AAA");
  assert.equal(elements.get("universe-retry").hidden, true);
  console.log(JSON.stringify({
    attempts: universeAttempts,
    count: elements.get("universe-count").textContent,
    retryHidden: elements.get("universe-retry").hidden,
  }));
} else if (mode === "stock-error-then-retry") {
  assert.equal(elements.get("universe-count").textContent, "1/1");
  assert.equal(elements.get("stock-retry").hidden, false);
  elements.get("stock-retry").dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(elements.get("universe-count").textContent, "1/1");
  assert.match(elements.get("research-status").textContent, /2026-07-22/);
  assert.equal(elements.get("stock-retry").hidden, true);
  console.log(JSON.stringify({
    attempts: stockAttempts,
    count: elements.get("universe-count").textContent,
    retryHidden: elements.get("stock-retry").hidden,
  }));
} else if (mode === "marker-layers") {
  const markerTexts = () => markerControllers[0].markers.map((marker) => marker.text);
  const noneButton = markerPresetControls.find(
    (control) => control.dataset.markerPreset === "none",
  );
  noneButton.dispatch("click");
  assert.deepEqual(markerTexts(), ["预测起点 · 下跌"]);
  const pocketControl = markerLayerControls.find(
    (control) => control.dataset.markerLayer === "pocket_pivot",
  );
  pocketControl.checked = true;
  pocketControl.dispatch("change");
  assert.deepEqual(
    JSON.parse(storageValues.get("quant-workstation.chart-marker-layers")),
    ["pocket_pivot"],
  );
  pocketControl.checked = false;
  pocketControl.dispatch("change");
  const topRiskControl = markerLayerControls.find(
    (control) => control.dataset.markerLayer === "top_risk",
  );
  topRiskControl.checked = true;
  topRiskControl.dispatch("change");
  assert.deepEqual(markerTexts(), [
    "顶部向下风险观察",
    "顶部向下高风险",
    "顶部向下风险确认",
    "顶部向下风险解除",
    "预测起点 · 下跌",
  ]);
  enButton.dispatch("click");
  assert.deepEqual(
    JSON.parse(storageValues.get("quant-workstation.chart-marker-layers")),
    ["top_risk"],
  );
  assert.deepEqual(markerTexts(), [
    "Top downside risk watch",
    "Top downside high risk",
    "Top downside risk confirmed",
    "Top downside risk cleared",
    "Forecast start · Down",
  ]);
  assert.equal(elements.get("marker-layer-count").textContent, "1/10 model layers shown");
  console.log(JSON.stringify({
    stored: JSON.parse(storageValues.get("quant-workstation.chart-marker-layers")),
    markerCount: elements.get("marker-layer-count").textContent,
  }));
}
