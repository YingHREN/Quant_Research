import { getLocale, t } from "./i18n.js";

const GROUPS = Object.freeze([
  ["primary", "modelOutput.group.primary"],
  ["downside", "modelOutput.group.downside"],
  ["bullish_structure", "modelOutput.group.bullish"],
]);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function translated(key, locale, fallback = "—") {
  if (!key) return fallback;
  const value = t(key, {}, locale);
  return value === key ? fallback : value;
}

function enumLabel(prefix, value, locale) {
  if (value === null || value === undefined || value === "") return "—";
  return t(`${prefix}.${value}`, {}, locale);
}

function percent(value, locale) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat(locale, {
    style: "percent",
    maximumFractionDigits: 2,
    signDisplay: "always",
  }).format(number);
}

function number(value, locale) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2 }).format(numeric);
}

function scoreText(model, locale) {
  if (model.score === null || model.score === undefined) return "—";
  const maximum = model.maximum_score ?? 100;
  return t("modelOutput.value.score", {
    score: model.score,
    maximum,
  }, locale);
}

function labeledValue(label, value) {
  const item = element("div", "model-output-field");
  item.append(
    element("dt", "", label),
    element("dd", "", value),
  );
  return item;
}

function conditionLabel(condition, locale) {
  const candidates = [
    `modelOutput.condition.${condition}`,
    `forecast.bearishCondition.${condition}`,
    `forecast.decisionReason.${condition}`,
  ];
  for (const key of candidates) {
    const value = t(key, {}, locale);
    if (value !== key) return value;
  }
  return String(condition).replaceAll("_", " ");
}

function modelCard(model, locale, { open = false } = {}) {
  const card = element("details", "model-output-card");
  card.dataset.modelCard = model.key || "unknown";
  card.dataset.status = model.status || "unavailable";
  card.open = open;

  const summary = element("summary", "model-output-card-summary");
  const title = element(
    "strong",
    "model-output-card-title",
    translated(model.name_key, locale, model.key),
  );
  const badge = element(
    "span",
    `model-output-badge model-output-badge-${model.status || "unavailable"}`,
    enumLabel("modelOutput.status", model.status || "unavailable", locale),
  );
  summary.append(title, badge);

  const identity = element("p", "model-output-identity");
  identity.textContent = [
    model.key,
    model.version || t("modelOutput.value.noVersion", {}, locale),
    enumLabel("modelOutput.kind", model.kind, locale),
    enumLabel("modelOutput.lifecycle", model.lifecycle, locale),
  ].join(" · ");

  const values = element("dl", "model-output-fields");
  if (model.predicted_return !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.predictedReturn", {}, locale),
      percent(model.predicted_return, locale),
    ));
  }
  if (model.direction !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.rawDirection", {}, locale),
      enumLabel("forecast.direction", model.direction, locale),
    ));
  }
  if (model.final_direction !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.finalDirection", {}, locale),
      enumLabel("forecast.direction", model.final_direction, locale),
    ));
  }
  if (model.action !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.action", {}, locale),
      enumLabel("modelOutput.action", model.action, locale),
    ));
  }
  if (model.score !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.score", {}, locale),
      scoreText(model, locale),
    ));
  }
  if (model.threshold !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.threshold", {}, locale),
      number(model.threshold, locale),
    ));
  }
  if (model.horizon_sessions !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.horizon", {}, locale),
      t("forecast.value.horizon", { sessions: model.horizon_sessions }, locale),
    ));
  }
  if (model.training_sample_count !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.trainingSamples", {}, locale),
      number(model.training_sample_count, locale),
    ));
  }
  if (model.training_cutoff !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.trainingCutoff", {}, locale),
      model.training_cutoff || "—",
    ));
  }
  if (model.evidence_status !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.evidenceStatus", {}, locale),
      enumLabel("modelOutput.evidence", model.evidence_status, locale),
    ));
  }
  const hasEvaluatedEvidence = model.evidence_status !== "not_precomputed";
  if (hasEvaluatedEvidence && model.direction_accuracy !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.directionAccuracy", {}, locale),
      percent(model.direction_accuracy, locale),
    ));
  }
  if (hasEvaluatedEvidence && model.always_up_direction_accuracy !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.alwaysUpBaseline", {}, locale),
      percent(model.always_up_direction_accuracy, locale),
    ));
  }
  if (hasEvaluatedEvidence && model.balanced_accuracy !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.balancedAccuracy", {}, locale),
      percent(model.balanced_accuracy, locale),
    ));
  }
  if (hasEvaluatedEvidence && model.non_overlapping_sample_count !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.nonOverlappingEvidence", {}, locale),
      t("modelOutput.value.nonOverlappingEvidence", {
        accuracy: percent(model.non_overlapping_direction_accuracy, locale),
        samples: number(model.non_overlapping_sample_count, locale),
      }, locale),
    ));
  }
  if (model.state !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.state", {}, locale),
      enumLabel("forecast.persistentRiskState", model.state, locale),
    ));
  }
  if (model.memory_age_sessions !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.memoryAge", {}, locale),
      t("modelOutput.value.sessions", {
        sessions: model.memory_age_sessions ?? "—",
      }, locale),
    ));
  }
  if (model.risk_state !== undefined) {
    values.append(labeledValue(
      t("modelOutput.field.riskState", {}, locale),
      enumLabel("forecast.riskState", model.risk_state, locale),
    ));
  }
  values.append(labeledValue(
    t("modelOutput.field.timing", {}, locale),
    enumLabel("modelOutput.timing", model.timing, locale),
  ));

  const explanation = element("p", "model-output-explanation");
  explanation.textContent = translated(model.explanation_key, locale);
  const limitation = element("p", "model-output-limitation");
  limitation.textContent = t("modelOutput.value.limitation", {
    text: translated(model.limitation_key, locale),
  }, locale);

  card.append(summary, identity, values, explanation, limitation);

  const conditions = model.conditions || model.reasons;
  if (Array.isArray(conditions) && conditions.length) {
    const list = element("ul", "model-output-conditions");
    conditions.forEach((condition) => {
      list.append(element("li", "", conditionLabel(condition, locale)));
    });
    card.append(list);
  }
  if (model.kind === "rule_score" || model.kind === "remembered_state") {
    card.append(element(
      "p",
      "model-output-score-warning",
      t("modelOutput.ruleScoreDisclaimer", {}, locale),
    ));
  }
  return card;
}

function summaryStrip(outputs, date, locale) {
  const strip = element("div", "model-output-summary");
  const ridge = outputs.primary?.[0] || {};
  const decision = outputs.decision || {};
  strip.append(
    labeledValue(t("modelOutput.field.date", {}, locale), date || "—"),
    labeledValue(
      t("modelOutput.field.ridgeDirection", {}, locale),
      enumLabel("forecast.direction", ridge.direction, locale),
    ),
    labeledValue(
      t("modelOutput.field.ridgeReturn", {}, locale),
      percent(ridge.predicted_return, locale),
    ),
    labeledValue(
      t("modelOutput.field.finalDirection", {}, locale),
      enumLabel("forecast.direction", decision.final_direction, locale),
    ),
    labeledValue(
      t("modelOutput.field.action", {}, locale),
      enumLabel("modelOutput.action", decision.action, locale),
    ),
  );
  return strip;
}

export function renderModelOutputs(container, options = {}) {
  if (!container) return;
  const locale = options.locale || getLocale();
  const date = options.date || "—";
  const outputs = options.forecast?.model_outputs;
  const requestState = options.requestState || null;

  container.replaceChildren();
  if (requestState === "loading") {
    container.dataset.state = "loading";
    const loading = element("div", "model-output-state");
    loading.append(
      element("strong", "", date),
      element("span", "", t("modelOutput.loading", {}, locale)),
    );
    container.append(loading);
    return;
  }
  if (!outputs) {
    container.dataset.state = requestState === "error" ? "error" : "unavailable";
    container.append(element(
      "div",
      "model-output-state",
      t(
        requestState === "error" ? "modelOutput.error" : "modelOutput.empty",
        { date },
        locale,
      ),
    ));
    return;
  }

  container.dataset.state = "available";
  container.append(summaryStrip(outputs, date, locale));

  const grid = element("div", "model-output-groups");
  GROUPS.forEach(([key, labelKey]) => {
    const group = element("section", "model-output-group");
    group.append(element("h4", "", t(labelKey, {}, locale)));
    const cards = element("div", "model-output-cards");
    (outputs[key] || []).forEach((model, index) => {
      cards.append(modelCard(model, locale, { open: key === "primary" && index === 0 }));
    });
    group.append(cards);
    grid.append(group);
  });

  const decisionGroup = element("section", "model-output-group model-output-decision");
  decisionGroup.append(
    element("h4", "", t("modelOutput.group.decision", {}, locale)),
    modelCard(outputs.decision || {}, locale, { open: true }),
  );
  grid.append(decisionGroup);
  container.append(grid);
}
