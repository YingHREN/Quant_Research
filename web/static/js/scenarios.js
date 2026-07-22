import { getLocale, t } from "./i18n.js";

const PATHS = Object.freeze([
  ["pessimistic", "scenario.path.pessimistic", "#ff9b78"],
  ["median", "scenario.path.median", "#edf4f8"],
  ["optimistic", "scenario.path.optimistic", "#35c6a5"],
]);

let activeChart = null;

function humanize(value, locale) {
  if (value == null || value === "") return t("security.state.unavailable", {}, locale);
  const key = `scenario.missing.${value}`;
  const localized = t(key, {}, locale);
  if (localized !== key) return localized;
  return String(value).replaceAll("_", " ");
}

function methodologyText(provider, methodology, locale, params = {}, suffix = "") {
  const key = `scenario.methodology.${provider}${suffix}`;
  const localized = t(key, params, locale);
  if (localized !== key) return localized;
  return methodology ? String(methodology) : t("scenario.methodologyUnavailable", {}, locale);
}

export function scenarioView(payload, locale = getLocale()) {
  const provider = payload && payload.provider ? String(payload.provider) : "";
  const horizons = payload && payload.horizons && typeof payload.horizons === "object"
    ? Object.values(payload.horizons)
    : [];
  horizons.sort((left, right) => Number(left.horizon_sessions) - Number(right.horizon_sessions));

  const series = [];
  const horizonViews = horizons.map((horizon) => {
    const sessions = Number(horizon.horizon_sessions);
    if (horizon.available) {
      PATHS.forEach(([key, labelKey, color]) => {
        const points = horizon.paths && Array.isArray(horizon.paths[key]) ? horizon.paths[key] : [];
        if (!points.length) return;
        series.push({
          key,
          color,
          title: t(
            "scenario.seriesTitle",
            { sessions, label: t(labelKey, {}, locale) },
            locale,
          ),
          data: points
            .filter((point) => Number.isFinite(point.session) && Number.isFinite(point.price))
            .map((point) => ({ time: point.session + 1, value: point.price })),
        });
      });
    }
    const sampleCount = Number.isFinite(horizon.sample_count) ? horizon.sample_count : 0;
    return {
      label: t("scenario.sessions", { sessions }, locale),
      available: Boolean(horizon.available),
      sampleText: t(
        horizon.non_overlapping === false
          ? "scenario.samples.historical"
          : "scenario.samples.nonOverlapping",
        { count: sampleCount, suffix: sampleCount === 1 ? "" : "s" },
        locale,
      ),
      detail: horizon.available
        ? methodologyText(
          provider,
          horizon.methodology,
          locale,
          { count: sampleCount, sessions },
          ".horizon",
        )
        : humanize(horizon.missing_reason, locale),
    };
  });

  return {
    locale,
    methodology: methodologyText(provider, payload && payload.methodology, locale),
    observationDate: payload && payload.observation_date ? String(payload.observation_date) : "—",
    horizons: horizonViews,
    series,
  };
}

function appendText(parent, tagName, className, value) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  node.textContent = value;
  parent.append(node);
  return node;
}

function renderMetadata(container, view) {
  container.replaceChildren();
  appendText(container, "p", "scenario-methodology", view.methodology);
  appendText(
    container,
    "p",
    "scenario-observation",
    t("scenario.observationDate", { date: view.observationDate }, view.locale),
  );
  const list = document.createElement("ul");
  list.className = "scenario-meta-list";
  view.horizons.forEach((horizon) => {
    const item = document.createElement("li");
    item.dataset.state = horizon.available ? "available" : "missing";
    appendText(item, "strong", "", horizon.label);
    appendText(item, "span", "", horizon.sampleText);
    appendText(item, "small", "", horizon.detail);
    list.append(item);
  });
  container.append(list);

  if (view.series.length) {
    const key = document.createElement("ul");
    key.className = "scenario-key";
    view.series.forEach((series) => {
      const item = document.createElement("li");
      item.style.setProperty("--series-color", series.color);
      item.textContent = series.title;
      key.append(item);
    });
    container.append(key);
  }
}

function createScenarioChart(container, view) {
  const library = globalThis.LightweightCharts;
  if (!library || !view.series.length) {
    const message = document.createElement("p");
    message.className = "scenario-empty";
    message.textContent = view.series.length
      ? t("scenario.libraryUnavailable", {}, view.locale)
      : t("scenario.insufficientHorizons", {}, view.locale);
    container.append(message);
    return { destroy() {} };
  }

  const chart = library.createChart(container, {
    width: container.clientWidth || 640,
    height: container.clientHeight || 260,
    layout: { background: { color: "#0b141c" }, textColor: "#91a3b0" },
    grid: {
      vertLines: { color: "rgba(39, 55, 69, 0.45)" },
      horzLines: { color: "rgba(39, 55, 69, 0.45)" },
    },
    rightPriceScale: { borderColor: "#273745" },
    timeScale: {
      borderColor: "#273745",
      tickMarkFormatter: (time) => t(
        "scenario.sessionTick",
        { session: Number(time) - 1 },
        view.locale,
      ),
    },
    localization: {
      timeFormatter: (time) => t(
        "scenario.sessionFull",
        { session: Number(time) - 1 },
        view.locale,
      ),
    },
  });
  view.series.forEach((series) => {
    const line = chart.addSeries(library.LineSeries, {
      title: series.title,
      color: series.color,
      lineWidth: series.key === "median" ? 3 : 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    line.setData(series.data);
  });
  chart.timeScale().fitContent();

  let resizeObserver = null;
  const resize = () => chart.applyOptions({
    width: container.clientWidth || 640,
    height: container.clientHeight || 260,
  });
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(resize);
    resizeObserver.observe(container);
  } else if (typeof window !== "undefined") {
    window.addEventListener("resize", resize);
  }

  return {
    destroy() {
      if (resizeObserver) resizeObserver.disconnect();
      else if (typeof window !== "undefined") window.removeEventListener("resize", resize);
      chart.remove();
    },
  };
}

export function renderScenarios(payload, options = {}) {
  const chartContainer = options.chart || document.getElementById("scenario-chart");
  const metadataContainer = options.metadata || document.getElementById("scenario-meta");
  if (activeChart) activeChart.destroy();
  activeChart = null;
  const view = scenarioView(payload, options.locale || getLocale());
  if (chartContainer) {
    chartContainer.replaceChildren();
    activeChart = createScenarioChart(chartContainer, view);
  }
  if (metadataContainer) renderMetadata(metadataContainer, view);
  return activeChart;
}
