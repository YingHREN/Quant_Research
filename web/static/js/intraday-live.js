import { t } from "./i18n.js";

const POLL_INTERVAL_MS = 2000;

function text(element, value) {
  if (element) element.textContent = value == null || value === "" ? "—" : String(value);
}

function number(value, digits = 2) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : "—";
}

function renderLine(svg, rows) {
  if (!svg) return;
  svg.replaceChildren();
  const values = rows.map((row) => Number(row.close)).filter(Number.isFinite);
  if (!values.length) return;
  const low = Math.min(...values);
  const high = Math.max(...values);
  const span = high - low || 1;
  const points = values.map((value, index) => {
    const x = values.length === 1 ? 300 : (index / (values.length - 1)) * 600;
    const y = 92 - ((value - low) / span) * 84;
    return `${x},${y}`;
  }).join(" ");
  const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  line.setAttribute("points", points);
  line.setAttribute("class", "intraday-price-line");
  svg.append(line);
}

function renderVolume(svg, rows) {
  if (!svg) return;
  svg.replaceChildren();
  const maximum = Math.max(1, ...rows.map((row) => Number(row.volume) || 0));
  rows.forEach((row, index) => {
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    const width = 600 / Math.max(rows.length, 1);
    const height = ((Number(row.volume) || 0) / maximum) * 54;
    bar.setAttribute("x", String(index * width));
    bar.setAttribute("y", String(58 - height));
    bar.setAttribute("width", String(Math.max(1, width - 1)));
    bar.setAttribute("height", String(height));
    bar.setAttribute("class", Number(row.delta) >= 0 ? "intraday-volume-buy" : "intraday-volume-sell");
    svg.append(bar);
  });
}

export function createIntradayLiveController({ api, elements, locale }) {
  let subscriptions = null;
  let selectedTicker = null;
  let timer = null;
  let generation = 0;
  let destroyed = false;

  const currentLocale = () => locale();
  const subscribed = () => subscriptions?.user_symbols?.includes(selectedTicker)
    || subscriptions?.fixed_symbols?.includes(selectedTicker);

  function renderSubscriptions() {
    if (!subscriptions) return;
    const capacity = subscriptions.capacity || {};
    text(
      elements.summary,
      t("intraday.subscription.capacity", {
        used: capacity.used ?? 0,
        limit: capacity.limit ?? 30,
      }, currentLocale()),
    );
    elements.list.replaceChildren();
    (subscriptions.user_symbols || []).forEach((ticker) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "intraday-subscription-chip";
      chip.textContent = `${ticker} ×`;
      chip.addEventListener("click", () => replace(
        subscriptions.user_symbols.filter((value) => value !== ticker),
      ));
      elements.list.append(chip);
    });
    elements.toggle.disabled = !selectedTicker;
    elements.toggle.setAttribute("aria-pressed", String(Boolean(subscribed())));
    text(
      elements.toggle,
      t(
        subscribed() ? "intraday.subscription.remove" : "intraday.subscription.add",
        {},
        currentLocale(),
      ),
    );
  }

  function renderSnapshot(payload) {
    text(elements.state, t(`intraday.state.${payload.state}`, {}, currentLocale()));
    text(elements.lastTrade, number(payload.latest_trade?.price));
    text(elements.bid, number(payload.quote?.bid_price));
    text(elements.ask, number(payload.quote?.ask_price));
    text(elements.spread, number(payload.quote?.spread, 3));
    const pressure = payload.pressure || {};
    const score = Number(pressure.score);
    elements.pressureBar.style.setProperty("--pressure-position", `${50 + (Number.isFinite(score) ? score / 2 : 0)}%`);
    text(
      elements.pressureDetail,
      t("intraday.pressure", {
        score: Number.isFinite(score) ? number(score, 0) : "—",
        coverage: Number.isFinite(pressure.direction_coverage)
          ? number(pressure.direction_coverage * 100, 0)
          : "—",
      }, currentLocale()),
    );
    renderLine(elements.priceChart, payload.minutes || []);
    renderVolume(elements.volumeChart, payload.minutes || []);
  }

  async function replace(symbols) {
    elements.toggle.disabled = true;
    try {
      subscriptions = await api.replaceIntradaySubscriptions(symbols);
      renderSubscriptions();
      schedule(0);
    } finally {
      elements.toggle.disabled = false;
    }
  }

  async function poll(expectedGeneration) {
    if (destroyed || expectedGeneration !== generation || document.hidden || !subscribed()) return;
    try {
      const payload = await api.getIntradaySnapshot(selectedTicker, 120);
      if (expectedGeneration === generation) renderSnapshot(payload);
    } catch (_error) {
      if (expectedGeneration === generation) {
        text(elements.state, t("intraday.state.unavailable", {}, currentLocale()));
      }
    }
    schedule(POLL_INTERVAL_MS);
  }

  function schedule(delay = POLL_INTERVAL_MS) {
    clearTimeout(timer);
    if (destroyed || document.hidden || !subscribed()) return;
    const expectedGeneration = generation;
    timer = globalThis.setTimeout(() => poll(expectedGeneration), delay);
  }

  async function initialize() {
    try {
      subscriptions = await api.getIntradaySubscriptions();
      renderSubscriptions();
      schedule(0);
    } catch (_error) {
      text(elements.state, t("intraday.state.unavailable", {}, currentLocale()));
    }
  }

  function selectTicker(ticker) {
    selectedTicker = ticker || null;
    generation += 1;
    renderSubscriptions();
    schedule(0);
  }

  elements.toggle.addEventListener("click", () => {
    if (!selectedTicker || !subscriptions) return;
    const users = subscriptions.user_symbols || [];
    replace(
      users.includes(selectedTicker)
        ? users.filter((value) => value !== selectedTicker)
        : [...users, selectedTicker],
    );
  });
  const onVisibility = () => schedule(0);
  document.addEventListener("visibilitychange", onVisibility);

  return {
    initialize,
    selectTicker,
    render: renderSubscriptions,
    destroy() {
      destroyed = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    },
  };
}
