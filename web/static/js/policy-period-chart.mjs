const BAND_COLORS = Object.freeze([
  "rgba(85, 191, 255, .12)",
  "rgba(255, 184, 77, .12)",
  "rgba(193, 132, 255, .12)",
  "rgba(56, 215, 178, .12)",
]);

function timeKey(value) {
  if (value == null) return null;
  if (typeof value === "string") return value.slice(0, 10);
  if (typeof value === "object" && "year" in value) {
    return [
      String(value.year).padStart(4, "0"),
      String(value.month).padStart(2, "0"),
      String(value.day).padStart(2, "0"),
    ].join("-");
  }
  return String(value).slice(0, 10);
}

function visiblePeriods(periods, asof) {
  const asofDate = timeKey(asof);
  const asofTimestamp = Date.parse(asof);
  return (periods || []).filter((period) => {
    if (!period?.period_id || !period.start_date) return false;
    if (timeKey(period.start_date) > asofDate) return false;
    if (!period.available_at) return true;
    const available = Date.parse(period.available_at);
    return Number.isFinite(available)
      && Number.isFinite(asofTimestamp)
      && available <= asofTimestamp;
  });
}

export function policyBandSegments(periods, rows, asof) {
  const availableRows = (rows || [])
    .map((row) => ({ ...row, time: timeKey(row.time) }))
    .filter((row) => row.time && row.time <= timeKey(asof))
    .sort((left, right) => left.time.localeCompare(right.time));
  if (!availableRows.length) return [];
  const firstDate = availableRows[0].time;
  const lastDate = availableRows.at(-1).time;
  return visiblePeriods(periods, asof).flatMap((period) => {
    const start = timeKey(period.start_date);
    const end = period.end_date
      ? timeKey(period.end_date)
      : timeKey(asof);
    if (end < firstDate || start > lastDate) return [];
    const first = availableRows.find(
      (row) => row.time >= start && row.time <= end,
    );
    const last = availableRows.findLast(
      (row) => row.time >= start && row.time <= end,
    );
    if (!first || !last) return [];
    return [{
      period_id: String(period.period_id),
      label_zh: period.label_zh || period.label_en || period.period_id,
      label_en: period.label_en || period.label_zh || period.period_id,
      start_time: first.time,
      end_time: last.time,
      is_complete: Boolean(period.is_complete && period.end_date),
    }];
  });
}

export function periodForTime(periods, time, asof) {
  const selected = timeKey(time);
  if (!selected || selected > timeKey(asof)) return null;
  return visiblePeriods(periods, asof).find((period) => {
    const start = timeKey(period.start_date);
    const end = period.end_date
      ? timeKey(period.end_date)
      : timeKey(asof);
    return start <= selected && selected <= end;
  }) || null;
}

function chartOptions(element) {
  return {
    width: element.clientWidth,
    height: 320,
    layout: {
      background: { type: "solid", color: "#0b141c" },
      textColor: "#91a4b4",
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: "#1d303d" },
      horzLines: { color: "#1d303d" },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#1d303d" },
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
      borderColor: "#1d303d",
      timeVisible: false,
      shiftVisibleRangeOnNewBar: false,
    },
  };
}

function localizedLabel(segment, locale) {
  return locale === "zh-CN"
    ? segment.label_zh || segment.label_en
    : segment.label_en || segment.label_zh;
}

export function createPolicyPeriodChart({
  chartElement,
  overlayElement,
  translate,
  locale = "zh-CN",
  onPeriodSelect = null,
}) {
  if (!chartElement || !overlayElement) {
    throw new TypeError("Policy chart containers are required");
  }
  if (typeof LightweightCharts === "undefined") {
    throw new Error("Lightweight Charts is not available");
  }
  let currentLocale = locale;
  let pricePayload = { benchmark: "SPY", rows: [], asof: null };
  let periods = [];
  let segments = [];
  const chart = LightweightCharts.createChart(
    chartElement,
    chartOptions(chartElement),
  );
  const priceSeries = chart.addSeries(
    LightweightCharts.LineSeries,
    {
      title: `SPY · ${translate("market.policyChart.price")}`,
      color: "#8ea7ff",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    },
  );

  function renderBands() {
    const scale = chart.timeScale();
    const bands = [];
    for (const [index, segment] of segments.entries()) {
      const start = scale.timeToCoordinate(segment.start_time);
      const end = scale.timeToCoordinate(segment.end_time);
      if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
      const band = document.createElement("div");
      band.className = "policy-period-band";
      band.dataset.periodId = segment.period_id;
      band.dataset.state = segment.is_complete
        ? "complete"
        : "incomplete";
      band.style.left = `${Math.max(0, start)}px`;
      band.style.width = `${Math.max(2, end - start)}px`;
      band.style.background = BAND_COLORS[index % BAND_COLORS.length];
      const label = document.createElement("span");
      label.textContent = localizedLabel(segment, currentLocale);
      if (!segment.is_complete) {
        label.textContent += ` · ${translate(
          "market.policyMatrix.period.incomplete",
        )}`;
      }
      band.append(label);
      bands.push(band);
    }
    overlayElement.replaceChildren(...bands);
  }

  chart.timeScale().subscribeVisibleLogicalRangeChange(renderBands);
  chart.subscribeClick((param) => {
    const period = periodForTime(
      periods,
      param?.time,
      pricePayload.asof,
    );
    if (
      period
      && typeof onPeriodSelect === "function"
    ) {
      onPeriodSelect(period.period_id);
    }
  });

  let resizeObserver = null;
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => {
      chart.applyOptions({ width: chartElement.clientWidth });
      renderBands();
    });
    resizeObserver.observe(chartElement);
  }

  return {
    update(nextPricePayload, nextPeriods = []) {
      pricePayload = nextPricePayload || {
        benchmark: "SPY",
        rows: [],
        asof: null,
      };
      periods = nextPeriods || [];
      segments = policyBandSegments(
        periods,
        pricePayload.rows || [],
        pricePayload.asof,
      );
      priceSeries.applyOptions({
        title: `${pricePayload.benchmark || "SPY"} · ${translate(
          "market.policyChart.price",
        )}`,
      });
      priceSeries.setData(
        (pricePayload.rows || []).map((row) => ({
          time: row.time,
          value: Number(row.close),
        })).filter((row) => Number.isFinite(row.value)),
      );
      chart.timeScale().fitContent();
      renderBands();
    },
    setLocale(nextLocale) {
      currentLocale = nextLocale;
      priceSeries.applyOptions({
        title: `${pricePayload.benchmark || "SPY"} · ${translate(
          "market.policyChart.price",
        )}`,
      });
      renderBands();
    },
    destroy() {
      resizeObserver?.disconnect();
      chart.remove();
    },
  };
}
