const FIELD_ALIASES = {
  strictVcp: ["strict_vcp", "strictVcp"],
  tightPlatform: ["tight_platform", "tightPlatform"],
  nearPivot: ["near_pivot", "nearPivot"],
};

function firstDefined(row, keys) {
  for (const key of keys) {
    if (row[key] !== undefined) return row[key];
  }
  return undefined;
}

export function filterTickers(rows, query = "", filters = {}) {
  const normalizedQuery = String(query || "").trim().toUpperCase();
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!String(row.ticker || "").toUpperCase().includes(normalizedQuery)) return false;
    if (filters.strictVcp
        && !(firstDefined(row, FIELD_ALIASES.strictVcp) || row.shape_state === "strict_vcp")) return false;
    if (filters.tightPlatform
        && !(firstDefined(row, FIELD_ALIASES.tightPlatform) || row.shape_state === "tight_platform")) return false;
    if (filters.nearPivot
        && !(firstDefined(row, FIELD_ALIASES.nearPivot) || row.shape_state === "near_pivot")) return false;
    const fresh = row.fresh ?? (!row.inactive && Number(row.lag_days) === 0);
    if (filters.fresh && !fresh) return false;
    if (filters.inactive && !row.inactive) return false;
    return true;
  });
}

function sortableValue(row, key) {
  if (key === "shape_state") {
    return row.shape_state ?? row.shapeState ?? "";
  }
  return row[key] ?? null;
}

export function sortTickers(rows, key = "ticker", direction = "asc") {
  const multiplier = direction === "desc" ? -1 : 1;
  return [...(Array.isArray(rows) ? rows : [])].sort((left, right) => {
    const a = sortableValue(left, key);
    const b = sortableValue(right, key);
    if (a == null && b == null) return String(left.ticker).localeCompare(String(right.ticker));
    if (a == null) return 1;
    if (b == null) return -1;
    const comparison = typeof a === "number" && typeof b === "number"
      ? a - b
      : String(a).localeCompare(String(b), undefined, { numeric: true });
    return comparison === 0
      ? String(left.ticker).localeCompare(String(right.ticker))
      : comparison * multiplier;
  });
}

function appendText(parent, className, value) {
  const node = document.createElement("span");
  node.className = className;
  node.textContent = value;
  parent.append(node);
  return node;
}

function describeShape(row) {
  const labels = {
    strict_vcp: "Strict VCP",
    tight_platform: "Tight platform",
    near_pivot: "Near pivot",
    none: "No shape",
    inactive: "Inactive",
  };
  const state = row.shape_state || row.shapeState;
  if (state) return labels[state] || String(state);
  if (row.inactive) return "Inactive";
  return "Active history";
}

export function renderUniverse(container, rows, options = {}) {
  const selectedTicker = options.selectedTicker || null;
  const onSelect = typeof options.onSelect === "function" ? options.onSelect : () => {};
  container.replaceChildren();

  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "empty-list";
    empty.textContent = "No tickers match the current view.";
    container.append(empty);
    return;
  }

  const fragment = document.createDocumentFragment();
  rows.forEach((row) => {
    const item = document.createElement("li");
    const button = document.createElement("button");
    const ticker = String(row.ticker || "");
    button.type = "button";
    button.className = "universe-row";
    button.setAttribute("aria-current", ticker === selectedTicker ? "true" : "false");
    button.addEventListener("click", () => onSelect(ticker));

    const headline = document.createElement("span");
    headline.className = "ticker-line";
    appendText(headline, "ticker-symbol", ticker);
    const state = appendText(headline, "ticker-state", describeShape(row));
    state.dataset.state = row.inactive ? "inactive" : "active";

    const metadata = document.createElement("span");
    metadata.className = "ticker-meta";
    const percentile = row.momentum_percentile ?? row.momentumPercentile;
    appendText(metadata, "ticker-factor", percentile == null ? "Momentum —" : `Momentum P${percentile}`);
    appendText(metadata, "ticker-date", row.latest_date || "No date");

    button.append(headline, metadata);
    item.append(button);
    fragment.append(item);
  });
  container.append(fragment);
}
