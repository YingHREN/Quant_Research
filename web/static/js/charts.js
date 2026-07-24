import { formatChartTickDate, formatFullDate, getLocale, t } from "./i18n.js";
import {
  DEFAULT_FORECAST_HORIZON,
  FORECAST_HORIZONS,
  evaluationFor,
  forecastFor,
  forecastMarker,
  indexForecasts,
  renderForecastDetail,
} from "./forecasts.js";
import { factorValuesByDate, trendEvidence } from "./trend_evidence.js";

const RANGE_BARS = Object.freeze({
  "3m": 63,
  "6m": 126,
  "1y": 252,
  "2y": 504,
});

const FORECAST_LAYOUT_PADDING = DEFAULT_FORECAST_HORIZON;

const AXIS_GUTTER_PX = 12;

const COLORS = Object.freeze({
  background: "#111b24",
  text: "#91a3b0",
  grid: "rgba(58, 80, 98, 0.35)",
  up: "#35c6a5",
  down: "#ff7a7a",
  ema20: "#5cc8ff",
  sma50: "#f2bd5d",
  sma200: "#c084fc",
  pivot: "#ff9f43",
  strictPivot: "#ff9f43",
  platformPivot: "#f472b6",
  trendline: "#ff8ccf",
  reversal: "#f7d154",
  forecast: "#7dd3fc",
  volumeMa20: "#5cc8ff",
  volumeRatio: "#f2bd5d",
});

function finite(value) {
  return Number.isFinite(value);
}

function chartHeight(element) {
  return Math.max(1, element.clientHeight - AXIS_GUTTER_PX);
}

function seriesPoints(rows, field) {
  return rows
    .filter((row) => finite(row[field]))
    .map((row) => ({ time: row.time, value: row[field] }));
}

function whitespaceSeriesPoints(rows, field) {
  return rows.map((row) => (
    finite(row[field])
      ? { time: row.time, value: row[field] }
      : { time: row.time }
  ));
}

function timeKey(time) {
  if (typeof time === "string" || typeof time === "number") return String(time);
  if (time && Number.isInteger(time.year) && Number.isInteger(time.month) && Number.isInteger(time.day)) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  return null;
}

function numberText(value, maximumFractionDigits = 2) {
  if (!finite(value)) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits }).format(value);
}

function percentText(value, fraction = false) {
  if (!finite(value)) return "—";
  const percent = fraction ? value * 100 : value;
  return `${percent > 0 ? "+" : ""}${percent.toFixed(2)}%`;
}

function ratioDeltaText(value) {
  if (!finite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}×`;
}

function pointDeltaText(value) {
  if (!finite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)} pp`;
}

function crossText(value, locale) {
  return value === "above"
    ? t("chart.cross.above", {}, locale)
    : value === "below" ? t("chart.cross.below", {}, locale) : "—";
}

function booleanText(value, locale) {
  return t(value ? "common.yes" : "common.no", {}, locale);
}

function earlyReversalConditionsText(row, locale) {
  const conditions = Array.isArray(row?.early_reversal_conditions)
    ? row.early_reversal_conditions
    : [];
  if (!conditions.length) return "—";
  return conditions.map((code) => {
    const key = `chart.earlyReversal.condition.${code}`;
    const localized = t(key, {}, locale);
    return localized === key ? code : localized;
  }).join(" · ");
}

export function detailItems(row, locale = getLocale()) {
  return [
    { label: t("chart.field.open", {}, locale), value: numberText(row.open) },
    { label: t("chart.field.high", {}, locale), value: numberText(row.high) },
    { label: t("chart.field.low", {}, locale), value: numberText(row.low) },
    { label: t("chart.field.close", {}, locale), value: numberText(row.close) },
    { label: t("chart.field.return", {}, locale), value: percentText(row.daily_return, true) },
    { label: t("chart.field.trueRange", {}, locale), value: percentText(row.true_range_pct) },
    { label: t("chart.field.volume", {}, locale), value: numberText(row.volume, 0) },
    { label: t("chart.field.volumeChange", {}, locale), value: percentText(row.volume_change, true) },
    { label: t("chart.field.volumeVsMa20", {}, locale), value: finite(row.volume_ratio) ? `${row.volume_ratio.toFixed(2)}×` : "—" },
    { label: t("chart.field.volumeRatioChange", {}, locale), value: ratioDeltaText(row.volume_ratio_change) },
    { label: t("chart.field.volumeMa20", {}, locale), value: numberText(row.volume_ma20, 0) },
    { label: t("chart.field.ema20", {}, locale), value: numberText(row.ema20) },
    { label: t("chart.field.sma50", {}, locale), value: numberText(row.sma50) },
    { label: t("chart.field.sma200", {}, locale), value: numberText(row.sma200) },
    { label: t("chart.field.atr20", {}, locale), value: numberText(row.atr20) },
    { label: t("chart.field.pivot", {}, locale), value: numberText(row.pivot) },
    { label: t("chart.field.pivotDistance", {}, locale), value: percentText(row.pivot_distance_pct) },
    { label: t("chart.field.pivotDistanceChange", {}, locale), value: pointDeltaText(row.pivot_distance_change_pct) },
    { label: t("chart.field.ema20Cross", {}, locale), value: crossText(row.ema20_cross, locale) },
    { label: t("chart.field.sma50Cross", {}, locale), value: crossText(row.sma50_cross, locale) },
    { label: t("chart.field.priorHighResistance", {}, locale), value: numberText(row.prior_high_resistance) },
    { label: t("chart.field.priorHighBreakout", {}, locale), value: booleanText(row.prior_high_breakout, locale) },
    { label: t("chart.field.descendingTrendline", {}, locale), value: numberText(row.descending_trendline) },
    { label: t("chart.field.trendlineBreakout", {}, locale), value: booleanText(row.trendline_breakout, locale) },
    { label: t("chart.field.higherLowConfirmed", {}, locale), value: booleanText(row.higher_low_confirmed, locale) },
    { label: t("chart.field.reversalConditions", {}, locale), value: `${Number(row.reversal_signal_count) || 0}/3` },
    { label: t("chart.field.earlyReversalWatch", {}, locale), value: finite(row.early_reversal_score) ? `${row.early_reversal_score}/100` : "—" },
    { label: t("chart.field.earlyReversalState", {}, locale), value: t(row.early_reversal_watch ? "chart.earlyReversal.watching" : "chart.earlyReversal.inactive", {}, locale) },
    { label: t("chart.field.earlyReversalEvidence", {}, locale), value: earlyReversalConditionsText(row, locale) },
    { label: t("chart.field.trendlinePivots", {}, locale), value: row.trendline_high_1_date && row.trendline_high_2_date ? `${row.trendline_high_1_date} → ${row.trendline_high_2_date}` : "—" },
    { label: t("chart.field.latestHighConfirmed", {}, locale), value: row.latest_confirmed_high_confirmed_date || "—" },
    { label: t("chart.field.higherLowConfirmation", {}, locale), value: row.higher_low_confirmation_date || "—" },
    { label: t("chart.field.higherLowPivots", {}, locale), value: row.higher_low_previous_date && row.higher_low_latest_date ? `${row.higher_low_previous_date} ${numberText(row.higher_low_previous_price)} → ${row.higher_low_latest_date} ${numberText(row.higher_low_latest_price)}` : "—" },
  ];
}

function appendDetail(detailEl, label, value) {
  const item = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value;
  item.append(term, description);
  detailEl.append(item);
}

function renderDetail(detailEl, row, locked, locale, forecastOptions = {}) {
  detailEl.replaceChildren();
  if (!row) {
    detailEl.textContent = t("chart.empty", {}, locale);
    return;
  }

  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const date = document.createElement("strong");
  const state = document.createElement("span");
  date.textContent = row.time;
  state.textContent = t(locked ? "chart.locked" : "chart.hoverHint", {}, locale);
  heading.append(date, state);

  const values = document.createElement("dl");
  values.className = "crosshair-values";
  detailItems(row, locale).forEach((item) => appendDetail(values, item.label, item.value));

  detailEl.append(heading, values);
  renderForecastDetail(detailEl, { ...forecastOptions, locale });
}

function chartInteractionOptions(panLocked = false) {
  return {
    handleScroll: {
      mouseWheel: false,
      pressedMouseMove: !panLocked,
      horzTouchDrag: false,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: false,
      mouseWheel: true,
      pinch: true,
    },
  };
}

function chartOptions(element) {
  return {
    width: element.clientWidth,
    height: chartHeight(element),
    layout: {
      background: { type: "solid", color: COLORS.background },
      textColor: COLORS.text,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: COLORS.grid },
      horzLines: { color: COLORS.grid },
    },
    rightPriceScale: { borderColor: COLORS.grid },
    ...chartInteractionOptions(),
    timeScale: {
      borderColor: COLORS.grid,
      timeVisible: false,
      shiftVisibleRangeOnNewBar: false,
      tickMarkFormatter: formatChartTickDate,
    },
    localization: { timeFormatter: formatFullDate },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };
}

export function createLinkedCharts(priceEl, volumeEl, detailEl, options = {}) {
  if (!priceEl || !volumeEl || !detailEl) {
    throw new TypeError("Chart containers and detail element are required");
  }
  if (typeof LightweightCharts === "undefined") {
    throw new Error("Lightweight Charts is not available");
  }

  let locale = options.locale || getLocale();
  const forecastRequestDelayMs = finite(options.forecastRequestDelayMs)
    ? Math.max(0, options.forecastRequestDelayMs)
    : 120;
  const priceChart = LightweightCharts.createChart(priceEl, chartOptions(priceEl));
  const volumeChart = LightweightCharts.createChart(volumeEl, chartOptions(volumeEl));
  const candleSeries = priceChart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: COLORS.up,
    downColor: COLORS.down,
    wickUpColor: COLORS.up,
    wickDownColor: COLORS.down,
    borderVisible: false,
  });
  const volumeSeries = volumeChart.addSeries(LightweightCharts.HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "right",
  });
  const ema20Series = priceChart.addSeries(LightweightCharts.LineSeries, {
    title: "EMA20",
    color: COLORS.ema20,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const sma50Series = priceChart.addSeries(LightweightCharts.LineSeries, {
    title: "SMA50",
    color: COLORS.sma50,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const sma200Series = priceChart.addSeries(LightweightCharts.LineSeries, {
    title: "SMA200",
    color: COLORS.sma200,
    lineWidth: 1,
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const trendlineSeries = priceChart.addSeries(LightweightCharts.LineSeries, {
    title: t("chart.series.descendingResistance", {}, locale),
    color: COLORS.trendline,
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    priceLineVisible: false,
    lastValueVisible: true,
  });
  const forecastProjectionSeries = priceChart.addSeries(LightweightCharts.LineSeries, {
    title: t("chart.series.forecastProjection", {}, locale),
    visible: false,
    color: COLORS.forecast,
    lineWidth: 3,
    lineStyle: LightweightCharts.LineStyle.Solid,
    crosshairMarkerVisible: false,
    priceLineVisible: false,
    lastValueVisible: true,
    autoscaleInfoProvider: () => null,
  });
  const volumeMa20Series = volumeChart.addSeries(LightweightCharts.LineSeries, {
    title: t("chart.series.volumeMa20", {}, locale),
    color: COLORS.volumeMa20,
    lineWidth: 1,
    priceScaleId: "right",
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const volumeRatioSeries = volumeChart.addSeries(LightweightCharts.LineSeries, {
    title: t("chart.series.volumeRatio", {}, locale),
    color: COLORS.volumeRatio,
    lineWidth: 2,
    priceScaleId: "volume-ratio",
    priceLineVisible: false,
    lastValueVisible: true,
  });
  if (typeof volumeChart.priceScale === "function") {
    volumeChart.priceScale("volume-ratio").applyOptions({
      scaleMargins: { top: 0.05, bottom: 0.7 },
      borderVisible: false,
    });
  }
  const seriesMarkers = LightweightCharts.createSeriesMarkers(candleSeries, []);

  const priceScale = priceChart.timeScale();
  const volumeScale = volumeChart.timeScale();
  let rows = [];
  let rowByTime = new Map();
  let pivotPriceLines = [];
  let lockedTime = null;
  let selectedRange = "1y";
  let syncing = false;
  let syncingCrosshair = false;
  const pendingCrosshairEvents = new Map();
  let destroyed = false;
  let lastPayload = null;
  let displayedRow = null;
  let factorsByDate = new Map();
  let forecastIndex = indexForecasts(null);
  let fetchedForecastIndexes = new Map();
  let requestedForecastDates = new Set();
  let forecastRequestStates = new Map();
  let forecastRequestTimer = null;
  let forecastRequestTimerDate = null;
  let forecastHorizon = DEFAULT_FORECAST_HORIZON;
  let shapeMarkerData = [];
  let forecastMarkerData = null;
  let paintingDetail = false;
  let updatingChartData = false;

  function setPanLocked(locked) {
    const panLocked = Boolean(locked);
    const options = chartInteractionOptions(panLocked);
    priceChart.applyOptions(options);
    volumeChart.applyOptions(options);
    [priceEl, volumeEl].forEach((element) => {
      if (element.dataset) {
        element.dataset.panLocked = String(panLocked);
      } else {
        element.setAttribute?.("data-pan-locked", String(panLocked));
      }
    });
  }

  setPanLocked(false);

  function refreshMarkers() {
    const markers = [
      ...shapeMarkerData,
      ...(forecastMarkerData ? [forecastMarkerData] : []),
    ];
    markers.sort((left, right) => String(left.time).localeCompare(String(right.time)));
    seriesMarkers.setMarkers(markers);
  }

  function paintDetail(row, locked) {
    if (paintingDetail) return;
    paintingDetail = true;
    try {
      displayedRow = row;
      const date = row ? timeKey(row.time) : null;
      const selectedForecastIndex = date === null
        ? forecastIndex
        : fetchedForecastIndexes.get(date) || forecastIndex;
      const indexedForecast = date === null
        ? null
        : forecastFor(selectedForecastIndex, date, forecastHorizon);
      const forecast = (
        indexedForecast
        && typeof indexedForecast.asof_date === "string"
        && indexedForecast.asof_date !== date
      ) ? null : indexedForecast;
      const selectedRowIndex = date === null
        ? -1
        : rows.findIndex((candidate) => timeKey(candidate.time) === date);
      const evidence = trendEvidence(row, {
        rows,
        index: selectedRowIndex,
        factorsByDate,
      });
      if (forecast && date !== null) forecastRequestStates.delete(date);
      const canRequestForecast = (
        date !== null
        && (!forecast || typeof forecast.target_date !== "string")
        && typeof options.onForecastDate === "function"
      );
      if (
        canRequestForecast
        && !requestedForecastDates.has(date)
        && forecastRequestStates.get(date) !== "error"
      ) {
        forecastRequestStates.set(date, "loading");
      }
      forecastMarkerData = forecastMarker(forecast, date, locale);
      refreshMarkers();
      renderDetail(detailEl, row, locked, locale, {
        forecast,
        evaluation: evaluationFor(selectedForecastIndex, forecastHorizon),
        horizon: forecastHorizon,
        model: selectedForecastIndex.model,
        dateCoverage: selectedForecastIndex.dateCoverage,
        date,
        trendEvidence: evidence,
        requestState: date === null ? null : forecastRequestStates.get(date) || null,
      });
      if (forecastRequestTimer !== null) {
        clearTimeout(forecastRequestTimer);
        if (
          forecastRequestTimerDate !== null
          && forecastRequestTimerDate !== date
          && forecastRequestStates.get(forecastRequestTimerDate) === "loading"
        ) {
          forecastRequestStates.delete(forecastRequestTimerDate);
        }
        forecastRequestTimer = null;
        forecastRequestTimerDate = null;
      }
      if (
        canRequestForecast
        && !requestedForecastDates.has(date)
        && forecastRequestStates.get(date) === "loading"
      ) {
        forecastRequestTimerDate = date;
        forecastRequestTimer = setTimeout(() => {
          forecastRequestTimer = null;
          forecastRequestTimerDate = null;
          requestedForecastDates.add(date);
          Promise.resolve(options.onForecastDate(date))
            .then((payload) => {
              if (!payload || destroyed) return;
              const computed = payload?.forecasts?.date_coverage?.computed_dates;
              if (!Array.isArray(computed) || !computed.includes(date)) {
                forecastRequestStates.set(date, "error");
                if (timeKey(displayedRow?.time) === date) {
                  paintDetail(displayedRow, lockedTime !== null);
                }
                return;
              }
              forecastRequestStates.delete(date);
              setForecasts(payload);
            })
            .catch(() => {
              forecastRequestStates.set(date, "error");
              if (timeKey(displayedRow?.time) === date) {
                paintDetail(displayedRow, lockedTime !== null);
              }
            });
        }, forecastRequestDelayMs);
      }
    } finally {
      paintingDetail = false;
    }
  }

  function synchronizeRange(targetScale) {
    return (range) => {
      if (!range || syncing || destroyed) return;
      syncing = true;
      try {
        targetScale.setVisibleLogicalRange(range);
      } finally {
        syncing = false;
      }
    };
  }

  const priceRangeHandler = synchronizeRange(volumeScale);
  const volumeRangeHandler = synchronizeRange(priceScale);
  priceScale.subscribeVisibleLogicalRangeChange(priceRangeHandler);
  volumeScale.subscribeVisibleLogicalRangeChange(volumeRangeHandler);

  function rowForParam(param) {
    const key = timeKey(param && param.time);
    return key === null ? null : rowByTime.get(key) || null;
  }

  function synchronizeCrosshair(source, row) {
    if (syncingCrosshair || destroyed) return;
    syncingCrosshair = true;
    try {
      if (!row) {
        if (source !== priceChart) {
          pendingCrosshairEvents.set(priceChart, null);
          priceChart.clearCrosshairPosition();
        }
        if (source !== volumeChart) {
          pendingCrosshairEvents.set(volumeChart, null);
          volumeChart.clearCrosshairPosition();
        }
      } else if (source === priceChart) {
        pendingCrosshairEvents.set(volumeChart, timeKey(row.time));
        volumeChart.setCrosshairPosition(row.volume, row.time, volumeSeries);
      } else {
        pendingCrosshairEvents.set(priceChart, timeKey(row.time));
        priceChart.setCrosshairPosition(row.close, row.time, candleSeries);
      }
    } finally {
      syncingCrosshair = false;
    }
  }

  function pinLockedCrosshair() {
    const lockedRow = lockedTime === null ? null : rowByTime.get(lockedTime);
    if (!lockedRow || syncingCrosshair || destroyed) return;
    const time = timeKey(lockedRow.time);
    syncingCrosshair = true;
    try {
      pendingCrosshairEvents.set(priceChart, time);
      priceChart.setCrosshairPosition(
        lockedRow.close,
        lockedRow.time,
        candleSeries,
      );
      pendingCrosshairEvents.set(volumeChart, time);
      volumeChart.setCrosshairPosition(
        lockedRow.volume,
        lockedRow.time,
        volumeSeries,
      );
    } finally {
      syncingCrosshair = false;
    }
  }

  function handleCrosshair(source) {
    return (param) => {
      if (destroyed || paintingDetail || updatingChartData) return;
      if (pendingCrosshairEvents.has(source)) {
        const expectedTime = pendingCrosshairEvents.get(source);
        const eventTime = timeKey(param && param.time);
        if (expectedTime === eventTime) {
          pendingCrosshairEvents.delete(source);
          return;
        }
        pendingCrosshairEvents.delete(source);
      }
      if (syncingCrosshair) return;
      if (lockedTime !== null) {
        pinLockedCrosshair();
        return;
      }
      const row = rowForParam(param);
      synchronizeCrosshair(source, row);
      paintDetail(row || rows.at(-1) || null, false);
    };
  }

  function handleClick(param) {
    if (destroyed) return;
    const row = rowForParam(param);
    if (lockedTime !== null) {
      lockedTime = null;
      setPanLocked(false);
      paintDetail(row || rows.at(-1) || null, false);
      return;
    }
    if (!row) return;
    lockedTime = timeKey(row.time);
    setPanLocked(true);
    paintDetail(row, true);
  }

  const priceCrosshairHandler = handleCrosshair(priceChart);
  const volumeCrosshairHandler = handleCrosshair(volumeChart);
  priceChart.subscribeCrosshairMove(priceCrosshairHandler);
  volumeChart.subscribeCrosshairMove(volumeCrosshairHandler);
  priceChart.subscribeClick(handleClick);
  volumeChart.subscribeClick(handleClick);

  function resizeCharts() {
    if (destroyed) return;
    priceChart.applyOptions({ width: priceEl.clientWidth, height: chartHeight(priceEl) });
    volumeChart.applyOptions({ width: volumeEl.clientWidth, height: chartHeight(volumeEl) });
  }

  let resizeObserver = null;
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(resizeCharts);
    resizeObserver.observe(priceEl);
    resizeObserver.observe(volumeEl);
  } else if (typeof window !== "undefined") {
    window.addEventListener("resize", resizeCharts);
  }

  function setRange(range) {
    if (destroyed || (range !== "all" && !(range in RANGE_BARS))) return;
    selectedRange = range;
    if (!rows.length) return;
    if (range === "all") {
      priceScale.fitContent();
      volumeScale.fitContent();
      return;
    }
    const last = rows.length - 1;
    const first = Math.max(0, rows.length - RANGE_BARS[range]);
    priceScale.setVisibleLogicalRange({
      from: first,
      to: last + FORECAST_LAYOUT_PADDING,
    });
  }

  function renderDecorations(payload) {
    pivotPriceLines.forEach((line) => candleSeries.removePriceLine(line));
    pivotPriceLines = [];
    const levels = payload && payload.structures && payload.structures.key_levels;
    const configuredLevels = [
      [levels && levels.strict_vcp_pivot, "chart.pivot.strictVcp", COLORS.strictPivot],
      [levels && levels.tight_platform_pivot, "chart.pivot.tightPlatform", COLORS.platformPivot],
    ].filter(([price]) => finite(price));
    const fallbackPivot = [...rows].reverse().find((row) => finite(row.pivot));
    const visibleLevels = configuredLevels.length
      ? configuredLevels
      : fallbackPivot ? [[fallbackPivot.pivot, "chart.pivot.twentySession", COLORS.pivot]] : [];
    pivotPriceLines = visibleLevels.map(([price, titleKey, color]) => (
      candleSeries.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title: t(titleKey, {}, locale),
      })
    ));

    const annotations = payload && payload.structures && payload.structures.annotations;
    const structureMarkers = (Array.isArray(annotations) ? annotations : []).map((annotation) => ({
      time: annotation.time,
      position: annotation.type === "tight_platform" ? "belowBar" : "aboveBar",
      color: annotation.type === "tight_platform" ? COLORS.platformPivot : COLORS.strictPivot,
      shape: annotation.type === "tight_platform" ? "arrowUp" : "arrowDown",
      text: (() => {
        const key = `chart.shape.${annotation.type}`;
        const localized = t(key, {}, locale);
        return localized === key ? annotation.label || t("chart.shape.default", {}, locale) : localized;
      })(),
    }));
    const reversalMarkers = rows.flatMap((row) => {
      const markers = [];
      if (row.prior_high_breakout) {
        markers.push({
          time: row.time, position: "belowBar", color: COLORS.up, shape: "arrowUp",
          text: t("chart.reversal.priorHighBreakout", {}, locale),
        });
      }
      if (row.trendline_breakout) {
        markers.push({
          time: row.time, position: "belowBar", color: COLORS.trendline, shape: "arrowUp",
          text: t("chart.reversal.trendlineBreakout", {}, locale),
        });
      }
      if (row.higher_low_confirmed) {
        markers.push({
          time: row.time, position: "belowBar", color: COLORS.sma50, shape: "circle",
          text: t("chart.reversal.higherLow", {}, locale),
        });
      }
      if (row.early_reversal_watch) {
        markers.push({
          time: row.time, position: "aboveBar", color: COLORS.reversal, shape: "arrowUp",
          text: t("chart.earlyReversal.marker", { score: row.early_reversal_score }, locale),
        });
      }
      if (row.reversal_candidate) {
        markers.push({
          time: row.time, position: "aboveBar", color: COLORS.reversal, shape: "circle",
          text: t("chart.reversal.candidate", { count: row.reversal_signal_count }, locale),
        });
      }
      return markers;
    });
    shapeMarkerData = [...structureMarkers, ...reversalMarkers];
    refreshMarkers();
  }

  function setChartData(payload) {
    if (destroyed) return;
    updatingChartData = true;
    try {
      lastPayload = payload;
      rows = Array.isArray(payload && payload.chart) ? payload.chart : [];
      rowByTime = new Map(rows.map((row) => [timeKey(row.time), row]));
      lockedTime = null;
      pendingCrosshairEvents.clear();
      priceChart.clearCrosshairPosition();
      volumeChart.clearCrosshairPosition();
      setPanLocked(false);
      forecastIndex = indexForecasts(payload);
      factorsByDate = factorValuesByDate(payload);
      fetchedForecastIndexes = new Map();
      requestedForecastDates = new Set();
      forecastRequestStates = new Map();
      if (forecastRequestTimer !== null) clearTimeout(forecastRequestTimer);
      forecastRequestTimer = null;
      forecastRequestTimerDate = null;

      candleSeries.setData(rows.map((row) => ({
        time: row.time,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
      })));
      volumeSeries.setData(rows.map((row) => ({
        time: row.time,
        value: row.volume,
        color: row.close >= row.open ? COLORS.up : COLORS.down,
      })));
      ema20Series.setData(seriesPoints(rows, "ema20"));
      sma50Series.setData(seriesPoints(rows, "sma50"));
      sma200Series.setData(seriesPoints(rows, "sma200"));
      trendlineSeries.setData(whitespaceSeriesPoints(rows, "descending_trendline"));
      volumeMa20Series.setData(seriesPoints(rows, "volume_ma20"));
      volumeRatioSeries.setData(seriesPoints(rows, "volume_ratio"));

      renderDecorations(payload);
    } finally {
      updatingChartData = false;
    }

    paintDetail(rows.at(-1) || null, false);
    setRange(selectedRange);
  }

  function setForecasts(payload) {
    if (destroyed) return;
    const nextIndex = indexForecasts(payload);
    const computedDates = nextIndex.dateCoverage?.computed_dates;
    if (Array.isArray(computedDates)) {
      computedDates.forEach((date) => {
        const key = String(date);
        fetchedForecastIndexes.set(key, nextIndex);
        forecastRequestStates.delete(key);
      });
    }
    paintDetail(displayedRow || rows.at(-1) || null, lockedTime !== null);
  }

  function setForecastHorizon(horizon) {
    if (destroyed) return forecastHorizon;
    const normalized = Number(horizon);
    if (!FORECAST_HORIZONS.includes(normalized)) return forecastHorizon;
    forecastHorizon = normalized;
    paintDetail(displayedRow || rows.at(-1) || null, lockedTime !== null);
    setRange(selectedRange);
    pinLockedCrosshair();
    return forecastHorizon;
  }

  function getForecastHorizon() {
    return forecastHorizon;
  }

  function setLocale(nextLocale) {
    if (destroyed) return;
    locale = nextLocale || getLocale();
    const dateOptions = {
      timeScale: { tickMarkFormatter: formatChartTickDate },
      localization: { timeFormatter: formatFullDate },
    };
    priceChart.applyOptions(dateOptions);
    volumeChart.applyOptions(dateOptions);
    volumeMa20Series.applyOptions?.({ title: t("chart.series.volumeMa20", {}, locale) });
    volumeRatioSeries.applyOptions?.({ title: t("chart.series.volumeRatio", {}, locale) });
    trendlineSeries.applyOptions?.({ title: t("chart.series.descendingResistance", {}, locale) });
    forecastProjectionSeries.applyOptions?.({ title: t("chart.series.forecastProjection", {}, locale) });
    renderDecorations(lastPayload);
    paintDetail(displayedRow || rows.at(-1) || null, lockedTime !== null);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
    if (forecastRequestTimer !== null) clearTimeout(forecastRequestTimer);
    forecastRequestTimerDate = null;
    priceScale.unsubscribeVisibleLogicalRangeChange(priceRangeHandler);
    volumeScale.unsubscribeVisibleLogicalRangeChange(volumeRangeHandler);
    priceChart.unsubscribeCrosshairMove(priceCrosshairHandler);
    volumeChart.unsubscribeCrosshairMove(volumeCrosshairHandler);
    priceChart.unsubscribeClick(handleClick);
    volumeChart.unsubscribeClick(handleClick);
    if (resizeObserver) resizeObserver.disconnect();
    else if (typeof window !== "undefined") window.removeEventListener("resize", resizeCharts);
    priceChart.remove();
    volumeChart.remove();
  }

  return {
    setChartData,
    setForecasts,
    setForecastHorizon,
    getForecastHorizon,
    setRange,
    setLocale,
    destroy,
  };
}
