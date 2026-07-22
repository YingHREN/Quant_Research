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

function crossText(value) {
  return value === "above" ? "Crossed above" : value === "below" ? "Crossed below" : "—";
}

export function detailItems(row) {
  return [
    { label: "Open", value: numberText(row.open) },
    { label: "High", value: numberText(row.high) },
    { label: "Low", value: numberText(row.low) },
    { label: "Close", value: numberText(row.close) },
    { label: "Return", value: percentText(row.daily_return, true) },
    { label: "True range", value: percentText(row.true_range_pct) },
    { label: "Volume", value: numberText(row.volume, 0) },
    { label: "Volume change", value: percentText(row.volume_change, true) },
    { label: "Volume / MA20", value: finite(row.volume_ratio) ? `${row.volume_ratio.toFixed(2)}×` : "—" },
    { label: "Volume ratio change", value: ratioDeltaText(row.volume_ratio_change) },
    { label: "Volume MA20", value: numberText(row.volume_ma20, 0) },
    { label: "EMA20", value: numberText(row.ema20) },
    { label: "SMA50", value: numberText(row.sma50) },
    { label: "SMA200", value: numberText(row.sma200) },
    { label: "ATR20", value: numberText(row.atr20) },
    { label: "Pivot", value: numberText(row.pivot) },
    { label: "Pivot distance", value: percentText(row.pivot_distance_pct) },
    { label: "Pivot-distance change", value: pointDeltaText(row.pivot_distance_change_pct) },
    { label: "EMA20 cross", value: crossText(row.ema20_cross) },
    { label: "SMA50 cross", value: crossText(row.sma50_cross) },
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

function renderDetail(detailEl, row, locked) {
  detailEl.replaceChildren();
  if (!row) {
    detailEl.textContent = "No chart observations are available.";
    return;
  }

  const heading = document.createElement("div");
  heading.className = "detail-heading";
  const date = document.createElement("strong");
  const state = document.createElement("span");
  date.textContent = row.time;
  state.textContent = locked ? "Locked · click a chart to unlock" : "Hover or click to lock";
  heading.append(date, state);

  const values = document.createElement("dl");
  values.className = "crosshair-values";
  detailItems(row).forEach((item) => appendDetail(values, item.label, item.value));

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
    timeScale: { borderColor: COLORS.grid, timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
  };
}

export function createLinkedCharts(priceEl, volumeEl, detailEl) {
  if (!priceEl || !volumeEl || !detailEl) {
    throw new TypeError("Chart containers and detail element are required");
  }
  if (typeof LightweightCharts === "undefined") {
    throw new Error("Lightweight Charts is not available");
  }

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
    title: "Volume MA20",
    color: COLORS.volumeMa20,
    lineWidth: 1,
    priceScaleId: "right",
    priceLineVisible: false,
    lastValueVisible: false,
  });
  const volumeRatioSeries = volumeChart.addSeries(LightweightCharts.LineSeries, {
    title: "Volume ratio",
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
      renderDetail(detailEl, row || rows.at(-1) || null, false);
    };
  }

  function handleClick(param) {
    if (destroyed) return;
    const row = rowForParam(param);
    if (lockedTime !== null) {
      lockedTime = null;
      renderDetail(detailEl, row || rows.at(-1) || null, false);
      return;
    }
    if (!row) return;
    lockedTime = timeKey(row.time);
    renderDetail(detailEl, row, true);
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

  function setChartData(payload) {
    if (destroyed) return;
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

    pivotPriceLines.forEach((line) => candleSeries.removePriceLine(line));
    pivotPriceLines = [];
    const levels = payload && payload.structures && payload.structures.key_levels;
    const configuredLevels = [
      [levels && levels.strict_vcp_pivot, "Strict VCP pivot", COLORS.strictPivot],
      [levels && levels.tight_platform_pivot, "Tight-platform pivot", COLORS.platformPivot],
    ].filter(([price]) => finite(price));
    const fallbackPivot = [...rows].reverse().find((row) => finite(row.pivot));
    const visibleLevels = configuredLevels.length
      ? configuredLevels
      : fallbackPivot ? [[fallbackPivot.pivot, "20-session pivot", COLORS.pivot]] : [];
    pivotPriceLines = visibleLevels.map(([price, title, color]) => (
      candleSeries.createPriceLine({
        price,
        color,
        lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dashed,
        axisLabelVisible: true,
        title,
      })
    ));

    const annotations = payload && payload.structures && payload.structures.annotations;
    shapeMarkers.setMarkers((Array.isArray(annotations) ? annotations : []).map((annotation) => ({
      time: annotation.time,
      position: annotation.type === "tight_platform" ? "belowBar" : "aboveBar",
      color: annotation.type === "tight_platform" ? COLORS.platformPivot : COLORS.strictPivot,
      shape: annotation.type === "tight_platform" ? "arrowUp" : "arrowDown",
      text: annotation.label || "Shape",
    })));

    renderDetail(detailEl, rows.at(-1) || null, false);
    setRange(selectedRange);
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

  return { setChartData, setRange, destroy };
}
