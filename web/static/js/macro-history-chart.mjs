const COLORS = Object.freeze({
  background: "#0b141c",
  grid: "#1d303d",
  text: "#91a4b4",
  total: "#38d7b2",
  rates: "#55bfff",
  inflation_energy: "#ffb84d",
  credit_liquidity: "#c184ff",
  risk_aversion: "#ff7380",
  macro: "#f6c85f",
  benchmark: "#8ea7ff",
});

const COMPONENTS = Object.freeze([
  "rates",
  "inflation_energy",
  "credit_liquidity",
  "risk_aversion",
]);

function timeKey(value) {
  if (value == null) return null;
  if (typeof value === "string") return value.slice(0, 10);
  if (typeof value === "object" && "year" in value) {
    const month = String(value.month).padStart(2, "0");
    const day = String(value.day).padStart(2, "0");
    return `${value.year}-${month}-${day}`;
  }
  return String(value).slice(0, 10);
}

export function createSelectionState(rows = []) {
  let rowByTime = new Map(rows.map((row) => [timeKey(row.time), row]));
  let hoveredTime = rows.length ? timeKey(rows.at(-1).time) : null;
  let lockedTime = null;

  return {
    replaceRows(nextRows = []) {
      rowByTime = new Map(nextRows.map((row) => [timeKey(row.time), row]));
      if (lockedTime && !rowByTime.has(lockedTime)) lockedTime = null;
      if (!rowByTime.has(hoveredTime)) {
        hoveredTime = nextRows.length ? timeKey(nextRows.at(-1).time) : null;
      }
    },
    hover(time) {
      const key = timeKey(time);
      if (lockedTime === null && rowByTime.has(key)) hoveredTime = key;
      return this.selected();
    },
    toggleLock(time) {
      if (lockedTime !== null) {
        lockedTime = null;
        const key = timeKey(time);
        if (rowByTime.has(key)) hoveredTime = key;
      } else {
        const key = timeKey(time);
        if (rowByTime.has(key)) {
          hoveredTime = key;
          lockedTime = key;
        }
      }
      return this.selected();
    },
    unlock() {
      lockedTime = null;
      return this.selected();
    },
    selected() {
      const selectedTime = lockedTime || hoveredTime;
      return {
        time: selectedTime,
        row: rowByTime.get(selectedTime) || null,
        locked: lockedTime !== null,
      };
    },
  };
}

function chartOptions(element, height) {
  return {
    width: element.clientWidth,
    height,
    layout: {
      background: { type: "solid", color: COLORS.background },
      textColor: COLORS.text,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: COLORS.grid },
      horzLines: { color: COLORS.grid },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: COLORS.grid },
    leftPriceScale: {
      visible: true,
      borderColor: COLORS.grid,
    },
    handleScroll: {
      mouseWheel: true,
      pressedMouseMove: true,
      horzTouchDrag: true,
      vertTouchDrag: false,
    },
    handleScale: {
      axisPressedMouseMove: true,
      mouseWheel: true,
      pinch: true,
    },
    timeScale: {
      borderColor: COLORS.grid,
      timeVisible: false,
      shiftVisibleRangeOnNewBar: false,
    },
  };
}

function addLine(chart, title, color, options = {}) {
  return chart.addSeries(LightweightCharts.LineSeries, {
    title,
    color,
    lineWidth: options.lineWidth || 2,
    priceLineVisible: false,
    lastValueVisible: false,
    priceScaleId: options.priceScaleId || "right",
  });
}

export function chartSeriesData(rows, getter) {
  return rows.map((row) => {
    const raw = getter(row);
    const value = raw == null ? Number.NaN : Number(raw);
    return Number.isFinite(value)
      ? { time: row.time, value }
      : { time: row.time };
  });
}

function appendText(parent, tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value == null ? "—" : String(value);
  parent.append(node);
  return node;
}

function formatNumber(value, digits = 2) {
  return value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toFixed(digits);
}

function formatPercent(value) {
  return value == null || !Number.isFinite(Number(value))
    ? "—"
    : `${(Number(value) * 100).toFixed(1)}%`;
}

export function createMacroHistoryCharts({
  scoreElement,
  contextElement,
  detailElement,
  seriesSelect,
  unlockButton,
  translate,
  locale = "zh-CN",
}) {
  if (!scoreElement || !contextElement || !detailElement) {
    throw new TypeError("Macro chart containers are required");
  }
  if (typeof LightweightCharts === "undefined") {
    throw new Error("Lightweight Charts is not available");
  }

  let currentLocale = locale;
  let payload = { rows: [], series_catalog: {} };
  let selectedSeries = "DGS2";
  let synchronizingRange = false;
  let synchronizingCrosshair = false;
  const selection = createSelectionState();
  const scoreChart = LightweightCharts.createChart(
    scoreElement,
    chartOptions(scoreElement, 290),
  );
  const contextChart = LightweightCharts.createChart(
    contextElement,
    chartOptions(contextElement, 210),
  );
  const totalSeries = addLine(
    scoreChart,
    translate("market.macro.total"),
    COLORS.total,
    { lineWidth: 3 },
  );
  const componentSeries = Object.fromEntries(
    COMPONENTS.map((key) => [
      key,
      addLine(
        scoreChart,
        translate(`market.macro.component.${key}`),
        COLORS[key],
      ),
    ]),
  );
  const macroSeries = addLine(
    contextChart,
    translate(`market.macro.history.series.${selectedSeries}`),
    COLORS.macro,
    { lineWidth: 3 },
  );
  const benchmarkSeries = addLine(
    contextChart,
    "SPY",
    COLORS.benchmark,
    { priceScaleId: "left" },
  );

  for (const [value, color] of [
    [30, "#756c3c"],
    [50, "#8b6639"],
    [70, "#813f48"],
  ]) {
    totalSeries.createPriceLine({
      price: value,
      color,
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: String(value),
    });
  }

  function tr(key, params = {}) {
    return translate(key, params);
  }

  function renderDetail() {
    const { row, locked } = selection.selected();
    detailElement.replaceChildren();
    if (!row) {
      appendText(
        detailElement,
        "p",
        "market-empty",
        tr("market.macro.history.empty"),
      );
      return;
    }
    const header = document.createElement("div");
    header.className = "macro-history-detail-heading";
    appendText(header, "strong", "", row.time);
    appendText(
      header,
      "span",
      locked ? "macro-lock-state is-locked" : "macro-lock-state",
      tr(
        locked
          ? "market.macro.history.locked"
          : "market.macro.history.unlocked",
      ),
    );
    detailElement.append(header);

    const summary = document.createElement("div");
    summary.className = "macro-history-detail-grid";
    const selected = row.series?.[selectedSeries] || {};
    const values = [
      [tr("market.macro.total"), formatNumber(row.score, 1)],
      [tr("market.coverage"), formatPercent(row.coverage)],
      [
        tr("market.macro.history.state"),
        tr(`market.macro.state.${row.state || "unavailable"}`),
      ],
      [
        tr(`market.macro.history.series.${selectedSeries}`),
        formatNumber(selected.value),
      ],
      [
        tr("market.macro.history.observationDate"),
        selected.observation_date || "—",
      ],
      [
        tr("market.macro.history.availableAt"),
        selected.available_at || "—",
      ],
      [
        tr("market.macro.history.benchmarkValue", {
          benchmark: payload.benchmark || "SPY",
        }),
        formatNumber(row.benchmark_normalized),
      ],
    ];
    for (const [label, value] of values) {
      const item = document.createElement("div");
      appendText(item, "span", "", label);
      appendText(item, "strong", "", value);
      summary.append(item);
    }
    for (const key of COMPONENTS) {
      const component = row.components?.[key] || {};
      const item = document.createElement("div");
      appendText(
        item,
        "span",
        "",
        tr(`market.macro.component.${key}`),
      );
      appendText(item, "strong", "", formatNumber(component.score, 1));
      summary.append(item);
    }
    detailElement.append(summary);

    const evidenceHeading = document.createElement("h4");
    evidenceHeading.textContent = tr("market.macro.history.evidenceTitle");
    detailElement.append(evidenceHeading);
    const evidence = document.createElement("div");
    evidence.className = "macro-history-evidence";
    for (const item of row.evidence || []) {
      const card = document.createElement("article");
      card.dataset.state = item.state || "unavailable";
      const top = document.createElement("div");
      appendText(
        top,
        "strong",
        "",
        tr(`market.macro.evidence.${item.key}`),
      );
      appendText(
        top,
        "span",
        "",
        tr(`market.state.${item.state || "unavailable"}`),
      );
      card.append(top);
      appendText(
        card,
        "p",
        "",
        tr("market.macro.history.evidenceValues", {
          value: formatNumber(item.value),
          operator: item.operator === "le" ? "≤" : "≥",
          threshold: formatNumber(item.threshold),
          weight: formatNumber(item.weight, 0),
        }),
      );
      appendText(
        card,
        "small",
        "",
        tr("market.macro.history.evidenceSource", {
          observation: item.observation_date || "—",
          available: item.available_at || "—",
        }),
      );
      evidence.append(card);
    }
    detailElement.append(evidence);
  }

  function updateContextSeries() {
    macroSeries.applyOptions({
      title: tr(`market.macro.history.series.${selectedSeries}`),
    });
    macroSeries.setData(
      chartSeriesData(
        payload.rows || [],
        (row) => row.series?.[selectedSeries]?.value,
      ),
    );
    renderDetail();
  }

  function populateSeries() {
    const previous = selectedSeries;
    seriesSelect.replaceChildren();
    for (const key of Object.keys(payload.series_catalog || {})) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = tr(`market.macro.history.series.${key}`);
      seriesSelect.append(option);
    }
    selectedSeries = Object.hasOwn(payload.series_catalog || {}, previous)
      ? previous
      : seriesSelect.options[0]?.value || "DGS2";
    seriesSelect.value = selectedSeries;
  }

  function synchronizeRange(target) {
    return (range) => {
      if (!range || synchronizingRange) return;
      synchronizingRange = true;
      try {
        target.timeScale().setVisibleLogicalRange(range);
      } finally {
        synchronizingRange = false;
      }
    };
  }
  scoreChart.timeScale().subscribeVisibleLogicalRangeChange(
    synchronizeRange(contextChart),
  );
  contextChart.timeScale().subscribeVisibleLogicalRangeChange(
    synchronizeRange(scoreChart),
  );

  function rowFromParam(param) {
    const key = timeKey(param?.time);
    return (payload.rows || []).find((row) => row.time === key) || null;
  }

  function handleCrosshair(source, target, targetSeries, valueGetter) {
    return (param) => {
      if (synchronizingCrosshair) return;
      const row = rowFromParam(param);
      if (!row) return;
      selection.hover(row.time);
      renderDetail();
      const value = Number(valueGetter(row));
      if (!Number.isFinite(value)) return;
      synchronizingCrosshair = true;
      try {
        target.setCrosshairPosition(value, row.time, targetSeries);
      } finally {
        synchronizingCrosshair = false;
      }
    };
  }
  scoreChart.subscribeCrosshairMove(
    handleCrosshair(
      scoreChart,
      contextChart,
      benchmarkSeries,
      (row) => row.benchmark_normalized,
    ),
  );
  contextChart.subscribeCrosshairMove(
    handleCrosshair(
      contextChart,
      scoreChart,
      totalSeries,
      (row) => row.score,
    ),
  );

  function handleClick(param) {
    const row = rowFromParam(param);
    if (!row) return;
    selection.toggleLock(row.time);
    renderDetail();
  }
  scoreChart.subscribeClick(handleClick);
  contextChart.subscribeClick(handleClick);

  seriesSelect.addEventListener("change", () => {
    selectedSeries = seriesSelect.value;
    updateContextSeries();
  });
  unlockButton.addEventListener("click", () => {
    selection.unlock();
    renderDetail();
  });

  let resizeObserver = null;
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => {
      scoreChart.applyOptions({ width: scoreElement.clientWidth });
      contextChart.applyOptions({ width: contextElement.clientWidth });
    });
    resizeObserver.observe(scoreElement);
    resizeObserver.observe(contextElement);
  }

  return {
    update(nextPayload) {
      payload = nextPayload || { rows: [], series_catalog: {} };
      const rows = payload.rows || [];
      selection.replaceRows(rows);
      populateSeries();
      totalSeries.setData(chartSeriesData(rows, (row) => row.score));
      for (const key of COMPONENTS) {
        componentSeries[key].setData(
          chartSeriesData(rows, (row) => row.components?.[key]?.score),
        );
      }
      benchmarkSeries.applyOptions({ title: payload.benchmark || "SPY" });
      benchmarkSeries.setData(
        chartSeriesData(rows, (row) => row.benchmark_normalized),
      );
      updateContextSeries();
      scoreChart.timeScale().fitContent();
      contextChart.timeScale().fitContent();
    },
    setLocale(nextLocale) {
      currentLocale = nextLocale;
      scoreChart.applyOptions({ localization: { locale: currentLocale } });
      contextChart.applyOptions({ localization: { locale: currentLocale } });
      totalSeries.applyOptions({ title: tr("market.macro.total") });
      for (const key of COMPONENTS) {
        componentSeries[key].applyOptions({
          title: tr(`market.macro.component.${key}`),
        });
      }
      populateSeries();
      updateContextSeries();
    },
    destroy() {
      resizeObserver?.disconnect();
      scoreChart.remove();
      contextChart.remove();
    },
  };
}
