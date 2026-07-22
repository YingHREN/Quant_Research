import { formatFullDate, getLocale, t } from "./i18n.js";

const POPOVER_ID = "factor-popover";
const POPOVER_MARGIN = 8;
const POPOVER_CLOSE_DELAY = 100;
let factorPopover = null;
let popoverDocument = null;
let activeTrigger = null;
let activePinned = false;
let activeOpenReason = null;
let pendingPopoverClose = null;
let suppressFocusOpen = false;
let popoverPointerOver = false;
const triggerPresence = new Map();
let triggerInteractionSequence = 0;

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

const LEGACY_REJECTION_REASON_CODES = new Map([
  ["历史不足", "insufficient_history"],
  ["价格未站上MA50", "below_ma50"],
  ["价未站上MA50", "below_ma50"],
  ["MA50<MA200(非上升趋势)", "ma50_below_ma200"],
  ["MA50<MA200", "ma50_below_ma200"],
  ["距52周高>25%", "too_far_from_52_week_high"],
  ["距52周高>10%", "too_far_from_52_week_high"],
  ["近20日涨幅>12%(加速上涨非整理)", "accelerated_20_session_rise"],
  ["近20日涨幅>12%(加速上涨)", "accelerated_20_session_rise"],
  ["无合格base(深度/单边/长度不满足)", "no_qualifying_base"],
  ["base内峰谷不足", "insufficient_base_swings"],
  ["base内收缩腿<2", "insufficient_contraction_legs"],
  ["非横盘(净涨幅或效率比过高)", "not_sideways"],
]);

function legacyRejectionReasonCode(reason) {
  if (reason == null || reason === "") return null;
  const text = String(reason);
  if (LEGACY_REJECTION_REASON_CODES.has(text)) {
    return LEGACY_REJECTION_REASON_CODES.get(text);
  }
  if (text.startsWith("收缩腿未严格递减")) return "contractions_not_decreasing";
  if (text.startsWith("区间宽度")) return "platform_too_wide";
  return null;
}

function rejectionReason(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value.reject_reason ?? value.reason;
  if (raw == null || raw === "") return null;
  return {
    code: value.rejection_reason_code || legacyRejectionReasonCode(raw),
    raw: String(raw),
  };
}

function localizedRejectionReason(value, locale) {
  const rejection = rejectionReason(value);
  if (!rejection) return null;
  if (!rejection.code) return rejection.raw;
  const key = `factor.rejection.${rejection.code}`;
  const localized = t(key, {}, locale);
  return localized === key ? rejection.raw : localized;
}

function localizedFactorValue(factor, locale) {
  const reason = localizedRejectionReason(factor?.raw_value, locale);
  if (reason) return t("factor.rejectedValue", { reason }, locale);
  return factor?.formatted == null ? null : String(factor.formatted);
}

function localizedRawValue(value, locale) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return rawValueText(value);
  }
  const reason = localizedRejectionReason(value, locale);
  const localized = Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => key !== "rejection_reason_code")
      .map(([key, nested]) => [
        key,
        reason && (key === "reject_reason" || key === "reason") ? reason : nested,
      ]),
  );
  return rawValueText(localized);
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
  const supplied = entity?.i18n?.[locale]?.[field];
  if (supplied != null && supplied !== "") return String(supplied);
  const key = `${namespace}.${entity && entity.key}.${field}`;
  const localized = t(key, {}, locale);
  return localized === key ? fallback : localized;
}

function localizedDirection(factor, locale) {
  const supplied = factor?.i18n?.[locale]?.direction;
  if (supplied != null && supplied !== "") return String(supplied);
  const key = `factor.direction.${factor?.direction}`;
  const localized = t(key, {}, locale);
  return localized === key ? humanize(factor?.direction) : localized;
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
    const metadataEntity = typeof metadata === "object" && metadata
      ? metadata
      : { key: normalizedKey };
    const overview = typeof metadata === "string" ? true : Boolean(metadata && metadata.overview);
    return [{
      key: normalizedKey,
      label: localizedMetadata(
        metadataEntity,
        "factor.group",
        "label",
        suppliedLabel || humanize(normalizedKey),
        locale,
      ),
      methodology: localizedMetadata(
        metadataEntity,
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
  return (Array.isArray(results) ? results : []).map((factor) => {
    const formattedValue = localizedFactorValue(factor, locale);
    return ({
      key: factor.key == null ? "" : String(factor.key),
      label: localizedMetadata(
        factor,
        "factor.item",
        "label",
        factor.label || humanize(factor.key),
        locale,
      ),
      formattedValue: formattedValue == null ? "—" : formattedValue,
      rawValue: localizedRawValue(factor.raw_value, locale),
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
      window: localizedMetadata(
        factor, "factor.item", "window", factor.window || "—", locale,
      ),
      direction: localizedDirection(factor, locale),
      currentValue: formattedValue == null
        ? (factor.missing ? "—" : rawValueText(factor.raw_value))
        : formattedValue,
      missingReason: localizedMissingReason(factor.missing_reason, locale),
      missing: Boolean(factor.missing),
    });
  });
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

function appendPopoverField(list, label, value) {
  const item = document.createElement("div");
  item.className = "factor-popover-field";
  appendText(item, "dt", "", label);
  appendText(item, "dd", "", value);
  list.append(item);
}

function renderPopoverContent(explanation) {
  if (!factorPopover) return;
  factorPopover.replaceChildren();
  appendText(
    factorPopover, "h3", "factor-popover-title", explanation.label,
  );
  appendText(factorPopover, "p", "factor-popover-description", explanation.description);
  const details = document.createElement("dl");
  details.className = "factor-popover-details";
  appendPopoverField(details, t("factor.popover.methodology", {}, explanation.locale), explanation.methodology);
  appendPopoverField(details, t("factor.popover.window", {}, explanation.locale), explanation.window);
  appendPopoverField(details, t("factor.popover.direction", {}, explanation.locale), explanation.direction);
  appendPopoverField(details, t("factor.popover.currentValue", {}, explanation.locale), explanation.currentValue);
  appendPopoverField(
    details,
    t("factor.popover.dataDate", {}, explanation.locale),
    formatFullDate(explanation.observationDate),
  );
  appendPopoverField(details, t("factor.popover.version", {}, explanation.locale), explanation.version);
  if (explanation.missing) {
    appendPopoverField(
      details,
      t("factor.popover.missingReason", {}, explanation.locale),
      explanation.missingReason,
    );
  }
  factorPopover.append(details);
}

function positionFactorPopover() {
  if (!factorPopover || !activeTrigger || factorPopover.hidden) return;
  const triggerRect = activeTrigger.getBoundingClientRect?.();
  const popoverRect = factorPopover.getBoundingClientRect?.();
  if (!triggerRect || !popoverRect) return;
  const viewportWidth = globalThis.window?.innerWidth || 0;
  const viewportHeight = globalThis.window?.innerHeight || 0;
  const maxLeft = Math.max(POPOVER_MARGIN, viewportWidth - popoverRect.width - POPOVER_MARGIN);
  const left = Math.max(POPOVER_MARGIN, Math.min(triggerRect.left, maxLeft));
  const below = triggerRect.bottom + POPOVER_MARGIN;
  const above = triggerRect.top - popoverRect.height - POPOVER_MARGIN;
  const preferredTop = below + popoverRect.height <= viewportHeight - POPOVER_MARGIN
    ? below
    : above;
  const maxTop = Math.max(POPOVER_MARGIN, viewportHeight - popoverRect.height - POPOVER_MARGIN);
  const top = Math.max(POPOVER_MARGIN, Math.min(preferredTop, maxTop));
  factorPopover.style.left = `${Math.round(left)}px`;
  factorPopover.style.top = `${Math.round(top)}px`;
}

function cancelPopoverClose() {
  if (pendingPopoverClose == null) return;
  globalThis.clearTimeout(pendingPopoverClose);
  pendingPopoverClose = null;
}

function activeTriggerHasPresence() {
  const presence = activeTrigger && triggerPresence.get(activeTrigger);
  return Boolean(presence?.pointerOver || presence?.focused || popoverPointerOver);
}

function mostRecentPresentTrigger() {
  let candidate = null;
  triggerPresence.forEach((presence, trigger) => {
    if (presence.suppressed || (!presence.pointerOver && !presence.focused)) return;
    if (!candidate || presence.lastInteraction > candidate.presence.lastInteraction) {
      candidate = { trigger, presence };
    }
  });
  return candidate;
}

function restorePresentFactorPopover() {
  if (activePinned) return false;
  const candidate = mostRecentPresentTrigger();
  if (!candidate) return false;
  if (candidate.trigger !== activeTrigger) {
    openFactorPopover(candidate.trigger, candidate.presence.explanation, {
      reason: candidate.presence.lastReason,
    });
  }
  return true;
}

function closeFactorPopover({ restoreFocus = false, suppressTrigger = false } = {}) {
  cancelPopoverClose();
  const trigger = activeTrigger;
  const presence = trigger && triggerPresence.get(trigger);
  if (suppressTrigger && presence) presence.suppressed = true;
  if (trigger) trigger.setAttribute("aria-expanded", "false");
  activeTrigger = null;
  activePinned = false;
  activeOpenReason = null;
  popoverPointerOver = false;
  if (factorPopover) factorPopover.hidden = true;
  if (restoreFocus && trigger?.focus) {
    suppressFocusOpen = true;
    try {
      trigger.focus();
    } finally {
      suppressFocusOpen = false;
    }
  }
}

function schedulePopoverClose(trigger = activeTrigger) {
  if (!trigger || activePinned) return;
  cancelPopoverClose();
  pendingPopoverClose = globalThis.setTimeout(() => {
    pendingPopoverClose = null;
    if (activeTrigger === trigger && !activePinned && !activeTriggerHasPresence()) {
      if (!restorePresentFactorPopover()) closeFactorPopover();
    }
  }, POPOVER_CLOSE_DELAY);
}

function documentKeydown(event) {
  if (event.key === "Escape" && activeTrigger) {
    event.preventDefault?.();
    closeFactorPopover({
      restoreFocus: activeOpenReason === "click" || activeOpenReason === "keyboard",
      suppressTrigger: true,
    });
  }
}

function documentClick(event) {
  if (!activeTrigger) return;
  if (activeTrigger.contains?.(event.target) || factorPopover?.contains?.(event.target)) return;
  closeFactorPopover({ suppressTrigger: true });
}

function ensureFactorPopover() {
  if (!globalThis.document?.body) return null;
  if (factorPopover && popoverDocument === document) return factorPopover;
  if (popoverDocument && popoverDocument !== document) {
    popoverDocument.removeEventListener?.("keydown", documentKeydown);
    popoverDocument.removeEventListener?.("click", documentClick);
  }
  factorPopover = document.createElement("aside");
  factorPopover.id = POPOVER_ID;
  factorPopover.className = "factor-popover";
  factorPopover.hidden = true;
  factorPopover.setAttribute("role", "tooltip");
  factorPopover.addEventListener?.("pointerenter", () => {
    if (!activeTrigger) return;
    popoverPointerOver = true;
    cancelPopoverClose();
  });
  factorPopover.addEventListener?.("pointerleave", () => {
    popoverPointerOver = false;
    if (!activeTriggerHasPresence()) schedulePopoverClose();
  });
  document.body.append(factorPopover);
  popoverDocument = document;
  document.addEventListener?.("keydown", documentKeydown);
  document.addEventListener?.("click", documentClick);
  globalThis.window?.addEventListener?.("resize", positionFactorPopover);
  globalThis.window?.addEventListener?.("scroll", positionFactorPopover, true);
  return factorPopover;
}

function openFactorPopover(trigger, explanation, { pinned = false, reason = "hover" } = {}) {
  if (!ensureFactorPopover()) return;
  cancelPopoverClose();
  if (activeTrigger && activeTrigger !== trigger) {
    activeTrigger.setAttribute("aria-expanded", "false");
  }
  activeTrigger = trigger;
  activePinned = pinned;
  activeOpenReason = reason;
  trigger.setAttribute("aria-expanded", "true");
  renderPopoverContent(explanation);
  factorPopover.hidden = false;
  positionFactorPopover();
}

function activateFactorPopover(trigger, explanation, reason) {
  const presence = triggerPresence.get(trigger);
  if (presence) {
    presence.suppressed = false;
    presence.lastInteraction = ++triggerInteractionSequence;
    presence.lastReason = reason;
  }
  if (activeTrigger === trigger && activePinned) {
    closeFactorPopover({ suppressTrigger: true });
    return;
  }
  openFactorPopover(trigger, explanation, { pinned: true, reason });
}

function appendFactorInfo(parent, explanation) {
  ensureFactorPopover();
  const button = document.createElement("button");
  button.className = "factor-info";
  button.setAttribute("type", "button");
  button.setAttribute("aria-controls", POPOVER_ID);
  button.setAttribute("aria-describedby", POPOVER_ID);
  button.setAttribute("aria-expanded", "false");
  button.setAttribute(
    "aria-label", t("factor.popover.explainAria", { label: explanation.label }, explanation.locale),
  );
  button.textContent = "ⓘ";
  const presence = {
    pointerOver: false,
    focused: false,
    explanation,
    lastInteraction: 0,
    lastReason: "hover",
    suppressed: false,
  };
  triggerPresence.set(button, presence);
  button.addEventListener?.("pointerenter", () => {
    presence.pointerOver = true;
    presence.suppressed = false;
    presence.lastInteraction = ++triggerInteractionSequence;
    presence.lastReason = "hover";
    if (!activePinned) openFactorPopover(button, explanation, { reason: "hover" });
  });
  button.addEventListener?.("pointerleave", () => {
    presence.pointerOver = false;
    if (activeTrigger === button && !activePinned && !activeTriggerHasPresence()) {
      schedulePopoverClose(button);
    }
  });
  button.addEventListener?.("focus", () => {
    presence.focused = true;
    if (suppressFocusOpen) return;
    presence.suppressed = false;
    presence.lastInteraction = ++triggerInteractionSequence;
    presence.lastReason = "focus";
    if (!activePinned || activeTrigger !== button) {
      openFactorPopover(button, explanation, { reason: "focus" });
    }
  });
  button.addEventListener?.("blur", () => {
    presence.focused = false;
    if (activeTrigger === button && !activePinned && !activeTriggerHasPresence()) {
      if (!restorePresentFactorPopover()) closeFactorPopover();
    }
  });
  button.addEventListener?.("click", () => activateFactorPopover(button, explanation, "click"));
  button.addEventListener?.("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    activateFactorPopover(button, explanation, "keyboard");
  });
  parent.append(button);
  return button;
}

function appendFactorLabel(parent, tagName, className, explanation) {
  const container = document.createElement(tagName);
  if (className) container.className = className;
  appendText(container, "span", "factor-label-text", explanation.label);
  appendFactorInfo(container, explanation);
  parent.append(container);
  return container;
}

function factorExplanation(factor, locale) {
  const row = factorDetailRows([factor], locale)[0];
  return {
    ...row,
    locale,
    observationDate: factor.observation_date,
  };
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
      appendFactorLabel(heading, "span", "factor-heading-label", {
        ...factorExplanation(factor, locale), label,
      });
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
    appendFactorLabel(row, "td", "factor-label", { ...factor, locale });
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
  closeFactorPopover();
  triggerPresence.clear();
  triggerInteractionSequence = 0;
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
    const reason = localizedRejectionReason(value, locale);
    const entries = Object.entries(value)
      .filter(([nestedKey]) => nestedKey !== "rejection_reason_code")
      .map(([nestedKey, nestedValue]) => [
        nestedKey,
        reason && (nestedKey === "reject_reason" || nestedKey === "reason")
          ? reason
          : nestedValue,
      ]);
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
