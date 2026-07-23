export const TREND_EVIDENCE_STATES = Object.freeze({
  MET: "met",
  NEAR: "near",
  NOT_MET: "not_met",
  UNAVAILABLE: "unavailable",
});

const { MET, NEAR, NOT_MET, UNAVAILABLE } = TREND_EVIDENCE_STATES;

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function item(key, state, evidence = null, threshold = null) {
  return { key, state, evidence, threshold };
}

function distanceState(close, level, atr, crossed) {
  if (!finite(close) || !finite(level)) return UNAVAILABLE;
  if (crossed === true) return MET;
  if (!finite(atr) || atr <= 0) return NOT_MET;
  const distance = level - close;
  return distance >= 0 && distance <= atr * 0.5 ? NEAR : NOT_MET;
}

function dualLevelState(close, first, second, direction) {
  if (!finite(close) || !finite(first) || !finite(second)) return UNAVAILABLE;
  const firstMet = direction === "above" ? close > first : close < first;
  const secondMet = direction === "above" ? close > second : close < second;
  if (firstMet && secondMet) return MET;
  if (firstMet || secondMet) return NEAR;
  return NOT_MET;
}

function volumeState(row, direction) {
  if (!finite(row?.daily_return) || !finite(row?.volume_ratio)) return UNAVAILABLE;
  const directionMet = direction === "up" ? row.daily_return > 0 : row.daily_return < 0;
  if (!directionMet) return NOT_MET;
  if (row.volume_ratio >= 1.2) return MET;
  if (row.volume_ratio >= 1.0) return NEAR;
  return NOT_MET;
}

function lowerLowState(row) {
  const support = row?.higher_low_latest_price;
  if (!finite(row?.close) || !finite(support)) return UNAVAILABLE;
  if (row.close < support) return MET;
  if (finite(row.atr20) && row.close - support <= row.atr20 * 0.5) return NEAR;
  return NOT_MET;
}

function volatilityState(row) {
  if (
    !finite(row?.true_range_pct)
    || !finite(row?.atr20)
    || !finite(row?.close)
    || row.close <= 0
  ) {
    return { state: UNAVAILABLE, threshold: null };
  }
  const atrPercent = (row.atr20 / row.close) * 100;
  if (row.true_range_pct >= atrPercent * 1.25) {
    return { state: MET, threshold: atrPercent * 1.25 };
  }
  if (row.true_range_pct >= atrPercent) {
    return { state: NEAR, threshold: atrPercent };
  }
  return { state: NOT_MET, threshold: atrPercent };
}

function failedBreakoutState(row, rows, index) {
  if (!finite(row?.close) || !Array.isArray(rows) || index < 0) return UNAVAILABLE;
  const priorRows = rows.slice(Math.max(0, index - 10), index + 1);
  const breakout = [...priorRows].reverse().find(
    (candidate) => candidate?.prior_high_breakout === true
      && finite(candidate.prior_high_resistance),
  );
  if (!breakout) return NOT_MET;
  return row.close < breakout.prior_high_resistance ? MET : NOT_MET;
}

function dateMomentum(factorsByDate, date) {
  if (!(factorsByDate instanceof Map)) return null;
  const value = factorsByDate.get(date);
  if (!value || typeof value !== "object") return null;
  for (const key of ["momentum_6_1m", "momentum_3_1m", "momentum_12_1m"]) {
    if (finite(value[key])) return value[key];
  }
  return null;
}

function momentumState(value, direction) {
  if (!finite(value)) return UNAVAILABLE;
  if (direction === "up") {
    if (value > 0) return MET;
    if (value >= -0.02) return NEAR;
    return NOT_MET;
  }
  if (value < 0) return MET;
  if (value <= 0.02) return NEAR;
  return NOT_MET;
}

export function trendEvidence(row, options = {}) {
  const rows = Array.isArray(options.rows) ? options.rows : [];
  const index = Number.isInteger(options.index) ? options.index : -1;
  const momentum = dateMomentum(options.factorsByDate, row?.time);
  const volatility = volatilityState(row);

  return {
    upward: [
      item(
        "prior_high_breakout",
        distanceState(
          row?.close,
          row?.prior_high_resistance,
          row?.atr20,
          row?.prior_high_breakout,
        ),
        row?.close,
        row?.prior_high_resistance,
      ),
      item(
        "trendline_breakout",
        distanceState(
          row?.close,
          row?.descending_trendline,
          row?.atr20,
          row?.trendline_breakout,
        ),
        row?.close,
        row?.descending_trendline,
      ),
      item(
        "higher_low",
        typeof row?.higher_low_confirmed === "boolean"
          ? (row.higher_low_confirmed ? MET : NOT_MET)
          : UNAVAILABLE,
        row?.higher_low_latest_price,
        row?.higher_low_previous_price,
      ),
      item(
        "trend_support",
        dualLevelState(row?.close, row?.ema20, row?.sma50, "above"),
        row?.close,
        finite(row?.ema20) && finite(row?.sma50) ? Math.max(row.ema20, row.sma50) : null,
      ),
      item(
        "volume_confirmation",
        volumeState(row, "up"),
        row?.volume_ratio,
        1.2,
      ),
      item("momentum", momentumState(momentum, "up"), momentum, 0),
    ],
    downward: [
      item(
        "support_loss",
        dualLevelState(row?.close, row?.ema20, row?.sma50, "below"),
        row?.close,
        finite(row?.ema20) && finite(row?.sma50) ? Math.min(row.ema20, row.sma50) : null,
      ),
      item(
        "lower_low_risk",
        lowerLowState(row),
        row?.close,
        row?.higher_low_latest_price,
      ),
      item(
        "distribution_volume",
        volumeState(row, "down"),
        row?.volume_ratio,
        1.2,
      ),
      item(
        "volatility_expansion",
        volatility.state,
        row?.true_range_pct,
        volatility.threshold,
      ),
      item(
        "failed_breakout",
        failedBreakoutState(row, rows, index),
        row?.close,
        row?.prior_high_resistance,
      ),
      item("negative_momentum", momentumState(momentum, "down"), momentum, 0),
    ],
  };
}
