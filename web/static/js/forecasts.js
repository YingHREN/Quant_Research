import { getLocale, t } from "./i18n.js";
import { renderTrendEvidence } from "./trend_evidence.js";

export const FORECAST_HORIZONS = Object.freeze([5, 20, 60]);
export const DEFAULT_FORECAST_HORIZON = 20;

let currentIndex = emptyIndex();

function emptyIndex() {
  return Object.freeze({
    byDate: new Map(),
    evaluation: new Map(),
    model: null,
    dateCoverage: null,
    modelOutputRegistry: null,
  });
}

function dateKey(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (value && Number.isInteger(value.year) && Number.isInteger(value.month) && Number.isInteger(value.day)) {
    return `${value.year}-${String(value.month).padStart(2, "0")}-${String(value.day).padStart(2, "0")}`;
  }
  return null;
}

function supportedHorizon(value) {
  const horizon = Number(value);
  return FORECAST_HORIZONS.includes(horizon) ? horizon : null;
}

export function indexForecasts(payload) {
  const forecasts = payload?.forecasts || payload;
  const rawByDate = forecasts && forecasts.by_date;
  const byDate = new Map();
  if (rawByDate && typeof rawByDate === "object") {
    Object.entries(rawByDate).forEach(([date, values]) => {
      const horizons = new Map();
      if (values && typeof values === "object") {
        FORECAST_HORIZONS.forEach((horizon) => {
          const forecast = values[String(horizon)];
          if (forecast && typeof forecast === "object") horizons.set(horizon, forecast);
        });
      }
      if (horizons.size) byDate.set(date, horizons);
    });
  }
  const evaluation = new Map();
  const rawEvaluation = payload?.forecast_evaluation;
  if (rawEvaluation && typeof rawEvaluation === "object") {
    FORECAST_HORIZONS.forEach((horizon) => {
      const evidence = rawEvaluation[String(horizon)];
      if (evidence && typeof evidence === "object") evaluation.set(horizon, evidence);
    });
  }
  currentIndex = Object.freeze({
    byDate,
    evaluation,
    model: forecasts?.model || null,
    dateCoverage: forecasts?.date_coverage || null,
    modelOutputRegistry: payload?.model_output_registry || null,
  });
  return currentIndex;
}

export function forecastFor(first, second, third) {
  const hasExplicitIndex = first && first.byDate instanceof Map;
  const index = hasExplicitIndex ? first : currentIndex;
  const date = dateKey(hasExplicitIndex ? second : first);
  const horizon = supportedHorizon(hasExplicitIndex ? third : second);
  if (date === null || horizon === null) return null;
  return index.byDate.get(date)?.get(horizon) || null;
}

export function evaluationFor(index, horizon) {
  const normalized = supportedHorizon(horizon);
  return normalized === null ? null : index?.evaluation?.get(normalized) || null;
}

function finite(value) {
  return Number.isFinite(value);
}

function percent(value, fractionDigits = 1, signed = false) {
  if (!finite(value)) return "—";
  const amount = value * 100;
  return `${signed && amount > 0 ? "+" : ""}${amount.toFixed(fractionDigits)}%`;
}

function localizedCode(prefix, value, locale) {
  if (!value) return t("forecast.value.unavailable", {}, locale);
  const key = `${prefix}.${value}`;
  const translated = t(key, {}, locale);
  return translated === key ? String(value).replaceAll("_", " ") : translated;
}

function appendItem(list, label, value) {
  const item = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = value;
  item.append(term, description);
  list.append(item);
}

function modelText(forecast, model) {
  const key = forecast?.model_key || model?.key;
  const version = forecast?.model_version || model?.version;
  if (!key && !version) return "—";
  return [key, version].filter(Boolean).join(" · ");
}

function decisionFor(forecast) {
  const decision = forecast?.decision;
  return decision && typeof decision === "object" ? decision : null;
}

function adjustedByRiskPolicy(forecast) {
  const decision = decisionFor(forecast);
  if (decision) return decision.action !== "retain";
  return forecast?.direction_adjustment_reason === "bearish_turn_risk"
    && forecast?.raw_direction
    && forecast.raw_direction !== forecast.direction;
}

function bearishConditionText(forecast, locale) {
  const conditions = Array.isArray(forecast?.bearish_turn_conditions)
    ? forecast.bearish_turn_conditions
    : [];
  return conditions
    .map((condition) => localizedCode("forecast.bearishCondition", condition, locale))
    .join("、");
}

function renderEvidence(section, evidence, locale) {
  const title = document.createElement("strong");
  title.className = "forecast-evidence-title";
  title.textContent = t("forecast.evidence.title", {}, locale);
  const list = document.createElement("dl");
  list.className = "forecast-evidence";
  if (!evidence || evidence.unavailable_reason) {
    const status = evidence?.unavailable_reason
      ? localizedCode("forecast.evaluationUnavailable", evidence.unavailable_reason, locale)
      : t("forecast.value.unavailable", {}, locale);
    appendItem(list, t("forecast.field.evidenceStatus", {}, locale), status);
  } else {
    appendItem(list, t("forecast.field.coverage", {}, locale), percent(evidence.coverage));
    appendItem(list, t("forecast.field.directionAccuracy", {}, locale), percent(evidence.direction_accuracy));
    appendItem(list, t("forecast.field.mae", {}, locale), percent(evidence.mae, 2));
    const baseline = t("forecast.value.baselines", {
      zero: percent(evidence.zero_return_mae, 2),
      mean: percent(evidence.historical_mean_mae, 2),
    }, locale);
    appendItem(list, t("forecast.field.baselines", {}, locale), baseline);
    const period = evidence.evaluation_start && evidence.evaluation_end
      ? `${evidence.evaluation_start} — ${evidence.evaluation_end}`
      : "—";
    appendItem(list, t("forecast.field.samplePeriod", {}, locale), period);
    appendItem(list, t("forecast.field.evaluationSamples", {}, locale), String(evidence.sample_count ?? "—"));
    appendItem(list, t("forecast.field.evidenceModel", {}, locale), evidence.model_version || "—");
  }
  section.append(title, list);
}

export function renderForecastDetail(container, options = {}) {
  if (!container) return;
  const locale = options.locale || getLocale();
  const forecast = options.forecast || null;
  const available = forecast && forecast.direction && forecast.direction !== "unavailable";
  const requestState = options.requestState || null;
  const section = document.createElement("section");
  section.className = "forecast-detail";
  section.setAttribute?.("aria-label", t("forecast.detailAria", {}, locale));

  const heading = document.createElement("div");
  heading.className = "forecast-signal-heading";
  const direction = document.createElement("strong");
  direction.className = `forecast-direction forecast-direction-${available ? forecast.direction : "unavailable"}`;
  direction.textContent = requestState === "loading"
    ? t("forecast.request.loading", { date: options.date }, locale)
    : requestState === "error"
      ? t("forecast.request.error", { date: options.date }, locale)
      : available
        ? t("forecast.value.direction", {
      direction: localizedCode("forecast.direction", forecast.direction, locale),
        }, locale)
        : t("forecast.value.unavailable", {}, locale);
  const horizon = document.createElement("span");
  horizon.textContent = t("forecast.value.horizon", { sessions: options.horizon }, locale);
  heading.append(direction, horizon);

  const values = document.createElement("dl");
  values.className = "forecast-values";
  if (available) {
    const decision = decisionFor(forecast);
    const riskAdjusted = adjustedByRiskPolicy(forecast);
    if (decision) {
      appendItem(
        values,
        t("forecast.field.decisionAction", {}, locale),
        localizedCode("forecast.decisionAction", decision.action, locale),
      );
      appendItem(
        values,
        t("forecast.field.riskState", {}, locale),
        localizedCode("forecast.riskState", decision.risk_state, locale),
      );
      appendItem(
        values,
        t("forecast.field.persistentRiskScore", {}, locale),
        finite(decision.persistent_risk_score)
          ? `${Number(decision.persistent_risk_score).toFixed(0)}/100`
          : t("forecast.value.unavailable", {}, locale),
      );
      const memoryState = localizedCode(
        "forecast.persistentRiskState",
        decision.persistent_risk_state,
        locale,
      );
      const memoryAge = Number.isInteger(decision.persistent_risk_age_sessions)
        ? t(
          "forecast.value.riskMemory",
          { state: memoryState, sessions: decision.persistent_risk_age_sessions },
          locale,
        )
        : memoryState;
      appendItem(values, t("forecast.field.riskMemory", {}, locale), memoryAge);
      const reasons = Array.isArray(decision.reasons)
        ? decision.reasons
          .map((reason) => localizedCode("forecast.decisionReason", reason, locale))
          .join("、")
        : "";
      appendItem(
        values,
        t("forecast.field.decisionReasons", {}, locale),
        reasons || t("forecast.value.noRiskReason", {}, locale),
      );
      const sources = Array.isArray(decision.persistent_risk_sources)
        ? decision.persistent_risk_sources
          .map((source) => localizedCode("forecast.riskSource", source, locale))
          .join("、")
        : "";
      appendItem(
        values,
        t("forecast.field.riskSources", {}, locale),
        sources || t("forecast.value.noActiveRiskSource", {}, locale),
      );
      const sourceScores = [
        ["individual", decision.individual_risk_score],
        ["group", decision.group_risk_score],
        ["slow_decline", decision.slow_decline_risk_score],
      ]
        .filter(([, score]) => finite(score))
        .map(([source, score]) => (
          `${localizedCode("forecast.riskSourceShort", source, locale)} ${Number(score).toFixed(0)}`
        ))
        .join(" · ");
      appendItem(
        values,
        t("forecast.field.riskSourceScores", {}, locale),
        sourceScores || t("forecast.value.unavailable", {}, locale),
      );
      appendItem(
        values,
        t("forecast.field.decisionPolicy", {}, locale),
        [decision.policy_key, decision.policy_version].filter(Boolean).join(" · ") || "—",
      );
    }
    if (riskAdjusted) {
      appendItem(
        values,
        t("forecast.field.bearishTurnScore", {}, locale),
        t(
          "forecast.value.bearishTurnScore",
          { score: Number(forecast.bearish_turn_score).toFixed(0) },
          locale,
        ),
      );
      appendItem(
        values,
        t("forecast.field.rawDirection", {}, locale),
        localizedCode("forecast.direction", forecast.raw_direction, locale),
      );
      appendItem(
        values,
        t("forecast.field.rawPredictedReturn", {}, locale),
        percent(forecast.predicted_return, 2, true),
      );
      const conditionText = bearishConditionText(forecast, locale);
      if (conditionText) {
        appendItem(
          values,
          t("forecast.field.bearishConditions", {}, locale),
          conditionText,
        );
      }
    }
    if (finite(forecast.up_probability)) {
      appendItem(values, t("forecast.field.probability", {}, locale), percent(forecast.up_probability));
    } else {
      appendItem(
        values,
        t("forecast.field.confidenceReason", {}, locale),
        localizedCode("forecast.confidence", forecast.confidence_reason, locale),
      );
    }
    if (!riskAdjusted) {
      appendItem(values, t("forecast.field.predictedReturn", {}, locale), percent(forecast.predicted_return, 2, true));
    }
    appendItem(values, t("forecast.field.trainingSamples", {}, locale), String(forecast.training_sample_count ?? "—"));
    appendItem(values, t("forecast.field.trainingCutoff", {}, locale), forecast.training_cutoff || "—");
    appendItem(values, t("forecast.field.model", {}, locale), modelText(forecast, options.model));
  } else if (requestState === null) {
    const computedDates = options.dateCoverage?.computed_dates;
    const dateWasComputed = Array.isArray(computedDates)
      && computedDates.includes(String(options.date));
    const missingReason = forecast?.unavailable_reason
      || (!forecast && dateWasComputed ? options.model?.unavailable_reason : null)
      || (!forecast ? options.dateCoverage?.omitted_reason : null)
      || (!forecast ? options.model?.unavailable_reason : null);
    if (missingReason) {
      appendItem(
        values,
        t("forecast.field.unavailableReason", {}, locale),
        localizedCode("forecast.unavailable", missingReason, locale),
      );
    }
  }

  const disclaimer = document.createElement("p");
  disclaimer.className = "forecast-disclaimer";
  disclaimer.textContent = t("forecast.disclaimer", {}, locale);
  section.append(heading, values);
  renderTrendEvidence(section, options.trendEvidence, locale);
  renderEvidence(section, options.evaluation, locale);
  section.append(disclaimer);
  container.append(section);
}

export function forecastMarker(forecast, date, locale = getLocale()) {
  if (!forecast || !["up", "neutral", "down"].includes(forecast.direction)) return null;
  const styles = {
    up: { position: "belowBar", color: "#35c6a5", shape: "arrowUp" },
    neutral: { position: "aboveBar", color: "#91a3b0", shape: "circle" },
    down: { position: "aboveBar", color: "#ff7a7a", shape: "arrowDown" },
  };
  return {
    time: date,
    ...styles[forecast.direction],
    text: t("forecast.marker", {
      direction: localizedCode("forecast.direction", forecast.direction, locale),
    }, locale),
  };
}
