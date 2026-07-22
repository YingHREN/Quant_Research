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

function percentileText(factor) {
  const count = factor.peer_count ?? factor.percentile_peer_count;
  const peers = Number.isInteger(count) && count >= 0
    ? `${count} same-date peer${count === 1 ? "" : "s"}`
    : "peer count unavailable";
  const percentile = finite(factor.percentile)
    ? `${ordinal(factor.percentile * 100)} percentile`
    : "Unavailable";
  return `${percentile} · ${peers}`;
}

function normalizeGroupMetadata(groupMetadata) {
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
      label: suppliedLabel || humanize(normalizedKey),
      methodology: suppliedMethodology || "—",
      overview,
      factors: [],
    }];
  });
}

export function groupFactorResults(results, groupMetadata = []) {
  const factors = Array.isArray(results) ? results : [];
  const configuredGroups = normalizeGroupMetadata(groupMetadata);
  const known = new Map(configuredGroups.map((group) => [group.key, group]));
  const other = {
    key: "other",
    label: "Other",
    methodology: "Unconfigured factor groups remain visible in the detail table.",
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

export function factorDetailRows(results) {
  return (Array.isArray(results) ? results : []).map((factor) => ({
    key: factor.key == null ? "" : String(factor.key),
    label: factor.label || humanize(factor.key),
    formattedValue: factor.formatted == null ? "—" : String(factor.formatted),
    rawValue: rawValueText(factor.raw_value),
    percentile: percentileText(factor),
    displayScore: finite(factor.display_score) ? factor.display_score.toFixed(1) : "—",
    observationDate: factor.observation_date || "—",
    description: factor.description || "—",
    methodology: factor.methodology || "—",
    version: factor.version || "—",
    missingReason: factor.missing_reason ? humanize(factor.missing_reason).toLowerCase() : "—",
    missing: Boolean(factor.missing),
  }));
}

export function overviewFactorGroups(results, groupMetadata = []) {
  return groupFactorResults(results, groupMetadata)
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

function renderOverview(container, results, groupMetadata) {
  container.replaceChildren();
  const groups = overviewFactorGroups(results, groupMetadata);

  if (!groups.length) {
    container.className = "empty-state";
    container.textContent = "No numeric display scores are available for this observation.";
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
      appendText(heading, "span", "", factor.label || humanize(factor.key));
      appendText(heading, "strong", "", factor.display_score.toFixed(1));
      const track = document.createElement("div");
      track.className = "factor-bar-track";
      track.setAttribute("role", "meter");
      track.setAttribute("aria-label", `${factor.label || humanize(factor.key)} display score`);
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

function renderDetailTable(tableBody, results) {
  tableBody.replaceChildren();
  const rows = factorDetailRows(results);
  if (!rows.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 9;
    cell.className = "empty-table-cell";
    cell.textContent = "No factor diagnostics are available.";
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
  if (overview) renderOverview(overview, results, options.groupMetadata);
  if (tableBody) renderDetailTable(tableBody, results);
}

function appendStructure(parent, key, value) {
  const item = document.createElement("div");
  item.className = "structure-item";
  appendText(item, "dt", "", humanize(key));
  const detail = document.createElement("dd");
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const nested = document.createElement("dl");
    nested.className = "structure-nested";
    const entries = Object.entries(value);
    if (entries.length) entries.forEach(([nestedKey, nestedValue]) => appendStructure(nested, nestedKey, nestedValue));
    else appendText(nested, "span", "", "—");
    detail.append(nested);
  } else {
    detail.textContent = value == null ? "—" : Array.isArray(value) ? rawValueText(value) : String(value);
  }
  item.append(detail);
  parent.append(item);
}

export function renderStructures(structures, container = document.getElementById("structure-content")) {
  if (!container) return;
  container.replaceChildren();
  const entries = structures && typeof structures === "object" ? Object.entries(structures) : [];
  if (!entries.length) {
    container.className = "empty-state";
    container.textContent = "No structure diagnostics are available.";
    return;
  }
  container.className = "structure-grid";
  const list = document.createElement("dl");
  entries.forEach(([key, value]) => appendStructure(list, key, value));
  container.append(list);
}
