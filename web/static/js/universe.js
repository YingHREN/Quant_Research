import { getLocale, t } from "./i18n.js";

const FIELD_ALIASES = {
  strictVcp: ["strict_vcp", "strictVcp"],
  tightPlatform: ["tight_platform", "tightPlatform"],
  nearPivot: ["near_pivot", "nearPivot"],
};

const POOL_SCOPES = new Set(["all", "active", "research", "catalog"]);

function firstDefined(row, keys) {
  for (const key of keys) {
    if (row[key] !== undefined) return row[key];
  }
  return undefined;
}

function normalizedPoolScope(value) {
  const scope = String(value || "all");
  return POOL_SCOPES.has(scope) ? scope : "all";
}

function matchesPoolScope(membership, scope) {
  if (scope === "active") return Boolean(membership.active);
  if (scope === "research") return Boolean(membership.research);
  if (scope === "catalog") {
    return Boolean(
      membership.research_catalog
      && !membership.research
      && !membership.active
    );
  }
  return true;
}

export function classificationFor(row = {}, taxonomy = "sec") {
  const classification = row.sector_classification ?? row.sectorClassification;
  if (!classification || typeof classification !== "object") return null;
  const value = classification[taxonomy];
  return value && typeof value === "object" ? value : null;
}

export function filterTickers(rows, query = "", filters = {}) {
  const normalizedQuery = String(query || "").trim().toUpperCase();
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (!String(row.ticker || "").toUpperCase().includes(normalizedQuery)) return false;
    const selectedShapes = [];
    if (filters.strictVcp) {
      selectedShapes.push(
        Boolean(firstDefined(row, FIELD_ALIASES.strictVcp))
        || row.shape_state === "strict_vcp",
      );
    }
    if (filters.tightPlatform) {
      selectedShapes.push(
        Boolean(firstDefined(row, FIELD_ALIASES.tightPlatform))
        || row.shape_state === "tight_platform",
      );
    }
    if (filters.nearPivot) {
      selectedShapes.push(
        Boolean(firstDefined(row, FIELD_ALIASES.nearPivot))
        || row.shape_state === "near_pivot",
      );
    }
    if (selectedShapes.length && !selectedShapes.some(Boolean)) return false;
    const fresh = row.fresh ?? (!row.inactive && Number(row.lag_days) === 0);
    if (filters.fresh && !fresh) return false;
    if (filters.inactive && !(row.inactive || row.stale)) return false;
    const membership = row.pool_membership ?? row.poolMembership ?? {
      active: true,
      research: false,
    };
    const poolScope = normalizedPoolScope(filters.poolScope);
    if (!matchesPoolScope(membership, poolScope)) return false;
    const gateState = row.technical_gate?.state
      ?? row.technicalGate?.state
      ?? "missing";
    const selectedGateStates = [];
    if (filters.gatePass) selectedGateStates.push(gateState === "pass");
    if (filters.gateFail) selectedGateStates.push(gateState === "fail");
    if (filters.gateMissing) selectedGateStates.push(gateState === "missing");
    if (selectedGateStates.length && !selectedGateStates.some(Boolean)) return false;
    const rsThreshold = filters.rs90 ? 90 : filters.rs80 ? 80 : null;
    if (
      rsThreshold !== null
      && (!Number.isFinite(row.rs_rating) || row.rs_rating < rsThreshold)
    ) return false;
    const sectorKey = String(filters.sectorKey || "");
    if (sectorKey && sectorKey !== "all") {
      const classification = classificationFor(
        row,
        filters.sectorTaxonomy || "sec",
      );
      const actual = classification?.sector_key || "unclassified";
      if (actual !== sectorKey) return false;
    }
    return true;
  });
}

function sortableValue(row, key) {
  if (key === "shape_state") {
    return row.shape_state ?? row.shapeState ?? "";
  }
  if (key === "technical_gate_score") {
    const gate = row.technical_gate ?? row.technicalGate;
    return Number.isFinite(gate?.passed_conditions)
      ? gate.passed_conditions
      : null;
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

function describeShape(row, locale) {
  const keys = {
    strict_vcp: "universe.shape.strictVcp",
    tight_platform: "universe.shape.tightPlatform",
    near_pivot: "universe.shape.nearPivot",
    none: "universe.shape.none",
    unavailable: "universe.shape.unavailable",
  };
  const state = row.shape_state || row.shapeState;
  if (state) return keys[state] ? t(keys[state], {}, locale) : String(state);
  return t("universe.shape.none", {}, locale);
}

function sectorLabel(sectorKey, locale) {
  if (!sectorKey) return t("universe.sector.unclassified", {}, locale);
  const key = `market.sector.${sectorKey}`;
  const localized = t(key, {}, locale);
  return localized === key ? String(sectorKey).replaceAll("_", " ") : localized;
}

function poolState(row) {
  const membership = row.pool_membership ?? row.poolMembership ?? {
    active: true,
    research: false,
  };
  if (membership.active && membership.research) return "both";
  if (membership.research) return "research";
  if (membership.research_catalog && !membership.active) return "catalog";
  return "active";
}

function gateState(row) {
  const gate = row.technical_gate ?? row.technicalGate ?? {};
  const state = ["pass", "fail", "missing"].includes(gate.state)
    ? gate.state
    : "missing";
  return {
    state,
    passed: Number.isFinite(gate.passed_conditions)
      ? gate.passed_conditions
      : null,
    total: Number.isFinite(gate.condition_count) ? gate.condition_count : 4,
  };
}

export function describeTickerState(row = {}, locale = getLocale()) {
  return {
    status: t(
      row.inactive
        ? "security.state.inactive"
        : row.stale ? "security.state.stale" : "security.state.current",
      {},
      locale,
    ),
    shape: describeShape(row, locale),
  };
}

export function renderUniverse(container, rows, options = {}) {
  const selectedTicker = options.selectedTicker || null;
  const onSelect = typeof options.onSelect === "function" ? options.onSelect : () => {};
  const locale = options.locale || getLocale();
  const sectorTaxonomy = options.sectorTaxonomy || "sec";
  container.replaceChildren();

  if (!rows.length) {
    const empty = document.createElement("li");
    empty.className = "empty-list";
    empty.textContent = t("universe.noMatch", {}, locale);
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
    const description = describeTickerState(row, locale);
    const state = appendText(headline, "ticker-state", description.status);
    state.dataset.state = row.inactive ? "inactive" : row.stale ? "stale" : "current";
    const pool = poolState(row);
    const poolBadge = appendText(
      headline,
      "ticker-pool",
      t(`universe.pool.${pool}`, {}, locale),
    );
    poolBadge.dataset.pool = pool;
    const gate = gateState(row);
    const gateBadge = appendText(
      headline,
      "ticker-gate",
      gate.passed == null
        ? t(`universe.gate.${gate.state}`, {}, locale)
        : t(
          "universe.gate.score",
          { passed: gate.passed, total: gate.total },
          locale,
        ),
    );
    gateBadge.dataset.state = gate.state;
    gateBadge.title = t("universe.gate.explanation", {}, locale);
    appendText(headline, "ticker-shape", description.shape);

    const metadata = document.createElement("span");
    metadata.className = "ticker-meta";
    const sector = classificationFor(row, sectorTaxonomy);
    const sectorNode = appendText(
      metadata,
      "ticker-sector",
      sectorLabel(sector?.sector_key, locale),
    );
    sectorNode.dataset.state = sector ? "classified" : "unclassified";
    const percentile = row.momentum_percentile ?? row.momentumPercentile;
    appendText(
      metadata,
      "ticker-factor",
      percentile == null
        ? t("universe.momentumMissing", {}, locale)
        : t("universe.momentum", { percentile }, locale),
    );
    appendText(
      metadata,
      "ticker-rs",
      Number.isFinite(row.rs_rating)
        ? t("universe.rs.value", { rating: row.rs_rating }, locale)
        : t("universe.rs.unavailable", {}, locale),
    );
    appendText(metadata, "ticker-date", row.latest_date || t("universe.noDate", {}, locale));

    button.append(headline, metadata);
    item.append(button);
    fragment.append(item);
  });
  container.append(fragment);
}
