import { getLocale, t } from "./i18n.js";

function humanize(value) {
  if (value == null || value === "") return "—";
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function rawValueText(value) {
  if (value === null) return "null";
  if (value === undefined) return "undefined";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch (_error) {
      return String(value);
    }
  }
  return String(value);
}

function ordinal(value) {
  const rounded = Math.round(value);
  const modulo100 = rounded % 100;
  if (modulo100 >= 11 && modulo100 <= 13) return `${rounded}th`;
  if (rounded % 10 === 1) return `${rounded}st`;
  if (rounded % 10 === 2) return `${rounded}nd`;
  if (rounded % 10 === 3) return `${rounded}rd`;
  return `${rounded}th`;
}

function localizedMetadata(entity, namespace, field, fallback, locale) {
  const key = `${namespace}.${entity && entity.key}.${field}`;
  const localized = t(key, {}, locale);
  return localized === key ? fallback : localized;
}

function percentileText(factor, locale) {
  const count = factor.peer_count ?? factor.percentile_peer_count;
  const peers = Number.isInteger(count) && count >= 0
    ? t("factor.peers.sameDate", { count, suffix: count === 1 ? "" : "s" }, locale)
    : t("factor.peers.unavailable", {}, locale);
  const percentile = finite(factor.percentile)
    ? t(
      "factor.percentile.value",
      { percentile: locale === "en" ? ordinal(factor.percentile * 100) : Math.round(factor.percentile * 100) },
      locale,
    )
    : t("factor.percentile.unavailable", {}, locale);
  return `${percentile} · ${peers}`;
}

function normalizeGroupMetadata(groupMetadata, locale) {
  const seen = new Set();
  return (Array.isArray(groupMetadata) ? groupMetadata : []).flatMap((metadata) => {
    const key = typeof metadata === "string"
      ? metadata
      : metadata && (metadata.key ?? metadata.group);
    if (key == null || key === "" || seen.has(String(key))) return [];
    const normalizedKey = String(key);
    seen.add(normalizedKey);
    const suppliedLabel = typeof metadata === "object" && metadata ? metadata.label : null;
    const suppliedMethodology = typeof metadata === "object" && metadata ? metadata.methodology : null;
    const overview = typeof metadata === "string" ? true : Boolean(metadata && metadata.overview);
    return [{
      key: normalizedKey,
      label: localizedMetadata(
        { key: normalizedKey },
        "factor.group",
        "label",
        suppliedLabel || humanize(normalizedKey),
        locale,
      ),
      methodology: localizedMetadata(
        { key: normalizedKey },
        "factor.group",
        "methodology",
        suppliedMethodology || "—",
        locale,
      ),
      overview,
      factors: [],
    }];
  });
}

export function groupFactorResults(results, groupMetadata = [], locale = getLocale()) {
  const factors = Array.isArray(results) ? results : [];
  const configuredGroups = normalizeGroupMetadata(groupMetadata, locale);
  const known = new Map(configuredGroups.map((group) => [group.key, group]));
  const other = {
    key: "other",
    label: t("factor.group.other.label", {}, locale),
    methodology: t("factor.group.other.methodology", {}, locale),
    overview: false,
    factors: [],
  };

  factors.forEach((factor) => {
    const bucket = known.get(factor && factor.group) || other;
    bucket.factors.push(factor || {});
  });

  const groups = configuredGroups.filter((group) => group.factors.length);
  if (other.factors.length) groups.push(other);
  return groups;
}

function localizedMissingReason(reason, locale) {
  if (!reason) return "—";
  const key = `factor.missing.${reason}`;
  const localized = t(key, {}, locale);
  return localized === key ? humanize(reason).toLowerCase() : localized;
}

export function factorDetailRows(results, locale = getLocale()) {
  return (Array.isArray(results) ? results : []).map((factor) => ({
    key: factor.key == null ? "" : String(factor.key),
    label: localizedMetadata(
      factor,
      "factor.item",
      "label",
      factor.label || humanize(factor.key),
      locale,
    ),
    formattedValue: factor.formatted == null ? "—" : String(factor.formatted),
    rawValue: rawValueText(factor.raw_value),
    percentile: percentileText(factor, locale),
    displayScore: finite(factor.display_score) ? factor.display_score.toFixed(1) : "—",
    observationDate: factor.observation_date || "—",
    description: localizedMetadata(
      factor, "factor.item", "description", factor.description || "—", locale,
    ),
    methodology: localizedMetadata(
      factor, "factor.item", "methodology", factor.methodology || "—", locale,
    ),
    version: factor.version || "—",
    missingReason: localizedMissingReason(factor.missing_reason, locale),
    missing: Boolean(factor.missing),
  }));
}

export function overviewFactorGroups(results, groupMetadata = [], locale = getLocale()) {
  return groupFactorResults(results, groupMetadata, locale)
    .filter((group) => group.overview)
    .map((group) => ({
      ...group,
      factors: group.factors.filter(
        (factor) => factor.overview !== false && finite(factor.display_score),
      ),
    }))
    .filter((group) => group.factors.length);
}

function appendText(parent, tagName, className, value) {
  const node = document.createElement(tagName);
  if (className) node.className = className;
  node.textContent = value;
  parent.append(node);
  return node;
}

function renderOverview(container, results, groupMetadata, locale) {
  container.replaceChildren();
  const groups = overviewFactorGroups(results, groupMetadata, locale);

  if (!groups.length) {
    container.className = "empty-state";
    container.textContent = t("factor.overviewEmpty", {}, locale);
    return;
  }

  container.className = "factor-groups";
  const fragment = document.createDocumentFragment();
  groups.forEach((group) => {
    const card = document.createElement("section");
    card.className = "factor-group";
    appendText(card, "h3", "factor-group-title", group.label);
    appendText(card, "p", "factor-group-methodology", group.methodology);
    const list = document.createElement("ul");
    list.className = "factor-bars";
    group.factors.forEach((factor) => {
      const item = document.createElement("li");
      const heading = document.createElement("div");
      heading.className = "factor-bar-heading";
      const label = localizedMetadata(
        factor,
        "factor.item",
        "label",
        factor.label || humanize(factor.key),
        locale,
      );
      appendText(heading, "span", "", label);
      appendText(heading, "strong", "", factor.display_score.toFixed(1));
      const track = document.createElement("div");
      track.className = "factor-bar-track";
      track.setAttribute("role", "meter");
      track.setAttribute("aria-label", t("factor.displayScoreAria", { label }, locale));
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", "100");
      track.setAttribute("aria-valuenow", String(factor.display_score));
      const fill = document.createElement("span");
      fill.className = "factor-bar-fill";
      fill.style.width = `${Math.max(0, Math.min(100, factor.display_score))}%`;
      track.append(fill);
      item.append(heading, track);
      list.append(item);
    });
    card.append(list);
    fragment.append(card);
  });
  container.append(fragment);
}

function renderDetailTable(tableBody, results, locale) {
  tableBody.replaceChildren();
  const rows = factorDetailRows(results, locale);
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty-table-cell";
    cell.textContent = t("factor.tableEmpty", {}, locale);
    row.append(cell);
    tableBody.append(row);
    return;
  }

  const fragment = document.createDocumentFragment();
  rows.forEach((factor) => {
    const row = document.createElement("tr");
    if (factor.missing) row.dataset.state = "missing";
    appendText(row, "td", "factor-label", factor.label);
    appendText(row, "td", "factor-formatted", factor.formattedValue);
    appendText(row, "td", "factor-raw", factor.rawValue);
    appendText(row, "td", "", factor.percentile);
    appendText(row, "td", "numeric", factor.displayScore);
    appendText(row, "td", "", factor.observationDate);
    const description = document.createElement("td");
    appendText(description, "span", "factor-description", factor.description);
    appendText(description, "small", "factor-version", factor.version);
    row.append(description);
    appendText(row, "td", "factor-methodology", factor.methodology);
    appendText(row, "td", "missing-reason", factor.missingReason);
    fragment.append(row);
  });
  tableBody.append(fragment);
}

export function renderFactors(results, options = {}) {
  const overview = options.overview || document.getElementById("factor-overview");
  const tableBody = options.tableBody || document.getElementById("factor-table-body");
  const locale = options.locale || getLocale();
  if (overview) renderOverview(overview, results, options.groupMetadata, locale);
  if (tableBody) renderDetailTable(tableBody, results, locale);
}

function structureLabel(key, locale) {
  const translationKey = `structure.field.${key}`;
  const localized = t(translationKey, {}, locale);
  return localized === translationKey ? humanize(key) : localized;
}

function appendStructure(parent, key, value, locale) {
  const item = document.createElement("div");
  item.className = "structure-item";
  appendText(item, "dt", "", structureLabel(key, locale));
  const detail = document.createElement("dd");
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const nested = document.createElement("dl");
    nested.className = "structure-nested";
    const entries = Object.entries(value);
    if (entries.length) entries.forEach(
      ([nestedKey, nestedValue]) => appendStructure(nested, nestedKey, nestedValue, locale),
    );
    else appendText(nested, "span", "", "—");
    detail.append(nested);
  } else {
    detail.textContent = value == null ? "—" : Array.isArray(value) ? rawValueText(value) : String(value);
  }
  item.append(detail);
  parent.append(item);
}

export function renderStructures(
  structures,
  container = document.getElementById("structure-content"),
  locale = getLocale(),
) {
  if (!container) return;
  container.replaceChildren();
  const entries = structures && typeof structures === "object" ? Object.entries(structures) : [];
  if (!entries.length) {
    container.className = "empty-state";
    container.textContent = t("structure.noDiagnostics", {}, locale);
    return;
  }
  container.className = "structure-grid";
  const list = document.createElement("dl");
  entries.forEach(([key, value]) => appendStructure(list, key, value, locale));
  container.append(list);
}
