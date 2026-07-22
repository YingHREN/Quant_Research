import { formatChartTickDate, formatFullDate, getLocale, t } from "./i18n.js";

const RANGE_BARS = Object.freeze({
  "3m": 63,
  "6m": 126,
  "1y": 252,
  "2y": 504,
});

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
  volumeMa20: "#5cc8ff",
  volumeRatio: "#f2bd5d",
});

function finite(value) {
  return Number.isFinite(value);
}

function seriesPoints(rows, field) {
  return rows
    .filter((row) => finite(row[field]))
    .map((row) => ({ time: row.time, value: row[field] }));
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

function renderDetail(detailEl, row, locked, locale) {
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
}

function chartOptions(element) {
  return {
    width: element.clientWidth,
    height: element.clientHeight,
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
    timeScale: {
      borderColor: COLORS.grid,
      timeVisible: false,
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
  const shapeMarkers = LightweightCharts.createSeriesMarkers(candleSeries, []);

  const priceScale = priceChart.timeScale();
  const volumeScale = volumeChart.timeScale();
  let rows = [];
  let rowByTime = new Map();
  let pivotPriceLines = [];
  let lockedTime = null;
  let selectedRange = "1y";
  let syncing = false;
  let syncingCrosshair = false;
  let destroyed = false;
  let lastPayload = null;
  let displayedRow = null;

  function paintDetail(row, locked) {
    displayedRow = row;
    renderDetail(detailEl, row, locked, locale);
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
        if (source !== priceChart) priceChart.clearCrosshairPosition();
        if (source !== volumeChart) volumeChart.clearCrosshairPosition();
      } else if (source === priceChart) {
        volumeChart.setCrosshairPosition(row.volume, row.time, volumeSeries);
      } else {
        priceChart.setCrosshairPosition(row.close, row.time, candleSeries);
      }
    } finally {
      syncingCrosshair = false;
    }
  }

  function handleCrosshair(source) {
    return (param) => {
      if (syncingCrosshair || destroyed) return;
      const row = rowForParam(param);
      synchronizeCrosshair(source, row);
      if (lockedTime !== null) return;
      paintDetail(row || rows.at(-1) || null, false);
    };
  }

  function handleClick(param) {
    if (destroyed) return;
    const row = rowForParam(param);
    if (lockedTime !== null) {
      lockedTime = null;
      paintDetail(row || rows.at(-1) || null, false);
      return;
    }
    if (!row) return;
    lockedTime = timeKey(row.time);
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
    priceChart.applyOptions({ width: priceEl.clientWidth, height: priceEl.clientHeight });
    volumeChart.applyOptions({ width: volumeEl.clientWidth, height: volumeEl.clientHeight });
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
    priceScale.setVisibleLogicalRange({ from: first, to: last });
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
    shapeMarkers.setMarkers((Array.isArray(annotations) ? annotations : []).map((annotation) => ({
      time: annotation.time,
      position: annotation.type === "tight_platform" ? "belowBar" : "aboveBar",
      color: annotation.type === "tight_platform" ? COLORS.platformPivot : COLORS.strictPivot,
      shape: annotation.type === "tight_platform" ? "arrowUp" : "arrowDown",
      text: (() => {
        const key = `chart.shape.${annotation.type}`;
        const localized = t(key, {}, locale);
        return localized === key ? annotation.label || t("chart.shape.default", {}, locale) : localized;
      })(),
    })));
  }

  function setChartData(payload) {
    if (destroyed) return;
    lastPayload = payload;
    rows = Array.isArray(payload && payload.chart) ? payload.chart : [];
    rowByTime = new Map(rows.map((row) => [timeKey(row.time), row]));
    lockedTime = null;

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
    volumeMa20Series.setData(seriesPoints(rows, "volume_ma20"));
    volumeRatioSeries.setData(seriesPoints(rows, "volume_ratio"));

    renderDecorations(payload);

    paintDetail(rows.at(-1) || null, false);
    setRange(selectedRange);
  }

  function setLocale(nextLocale) {
    if (destroyed) return;
    locale = nextLocale || getLocale();
    volumeMa20Series.applyOptions?.({ title: t("chart.series.volumeMa20", {}, locale) });
    volumeRatioSeries.applyOptions?.({ title: t("chart.series.volumeRatio", {}, locale) });
    renderDecorations(lastPayload);
    paintDetail(displayedRow || rows.at(-1) || null, lockedTime !== null);
  }

  function destroy() {
    if (destroyed) return;
    destroyed = true;
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

  return { setChartData, setRange, setLocale, destroy };
}
