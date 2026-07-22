# Dashboard Localization and Point-in-Time Forecast Design

## Goal

Improve the quant dashboard in four connected areas:

1. make chart dates deterministic and unambiguous;
2. provide a complete Simplified Chinese interface with an English fallback;
3. explain every factor in context;
4. show a historically testable 5/20/60-session direction signal at the date under the chart cursor.

The feature remains a research aid. It must not present model output as investment advice, a guaranteed target, or validated alpha.

## User Experience

### Dates

- Price and volume chart tick labels use `MM-DD`.
- Crosshair and observation details use ISO `YYYY-MM-DD`.
- Formatting is explicit and must not depend on the browser or operating-system locale.
- The same date formatter is shared by the linked price and volume charts.

### Language

- The default language is Simplified Chinese (`zh-CN`).
- A header control switches between `中文` and `EN` without reloading the page.
- The selection is stored in local storage and restored on the next visit.
- Page labels, transient states, warnings, factor metadata, scenario descriptions, chart series names, accessibility labels, and error messages are localized.
- Stable technical identifiers and common market abbreviations remain unchanged: ticker symbols, VCP, EMA20, SMA50, SMA200, ATR20, OHLCV.
- Unknown server messages fall back safely to the original message instead of being hidden or mistranslated.

### Factor Explanations

- Every factor label has an adjacent information trigger.
- Hover, click, keyboard focus, `Enter`, and `Space` expose the same explanation.
- The explanation contains localized name, plain-language meaning, calculation or methodology, observation window, favorable direction, current value, data date, factor version, and missing reason when applicable.
- The trigger uses an accessible button and the popover is associated through ARIA attributes.
- Only one explanation is open at a time; `Escape`, outside click, or a second activation closes it.

### Direction Signal on the Chart

- A horizon selector offers 5, 20, and 60 trading sessions; 20 is the default.
- Moving the crosshair to a date requests or reads the point-in-time signal for that date and selected horizon.
- The chart displays an up, neutral, or down marker anchored to the selected date.
- The detail strip shows direction, probability or calibrated confidence when available, predicted return, training sample count, training cutoff, model version, and an explicit research disclaimer.
- Click-lock freezes both the OHLCV detail and direction signal; unlocking resumes hover behavior.
- When the model is unavailable, stale, or below the minimum sample threshold, the UI shows `暂不可评估` / `Unavailable` and never invents confidence.

## Localization Architecture

Add a small client-side localization module with:

- a message catalog keyed by stable identifiers;
- `t(key, parameters)` for text lookup and interpolation;
- explicit `formatChartTickDate` and `formatFullDate` helpers;
- `getLocale`, `setLocale`, and subscription support;
- local-storage persistence with validation and a `zh-CN` default.

Static template content uses localization keys or is updated by the localization controller on initialization. Dynamic renderers receive a translator or locale rather than embedding new language-specific branches. Factor and factor-group API metadata gain optional localized fields while preserving current English fields for compatibility.

## Prediction Architecture

### Interface

Introduce a forecast provider interface independent of the chart and factor registry. Its output contract is:

```text
ticker
asof_date
horizon_sessions
direction: up | neutral | down | unavailable
predicted_return
up_probability
confidence_status
training_sample_count
training_cutoff
model_key
model_version
unavailable_reason
```

The stock-detail endpoint supplies point-in-time forecast observations for chart dates and all supported horizons. The response may be sparse; the client must handle dates without a valid forecast.

### Features

The first model uses only factors available through the selected date:

- trend: close versus EMA20, SMA50, and SMA200;
- momentum: 3-1, 6-1, and 12-1 month momentum;
- structure: strict VCP, tight platform, and pivot distance;
- volume: volume ratio and related participation diagnostics;
- risk: ATR20 and realized volatility.

Feature definitions remain registered and versioned so later factors can be added without changing the forecasting or chart interfaces.

### Target and Model

- Targets are forward close-to-close returns over 5, 20, and 60 sessions.
- A regularized regression estimates forward return. Direction is derived from a horizon-specific neutral band documented with the model version.
- A probability or calibrated confidence is shown only when an out-of-sample calibration procedure and sufficient calibration samples are available.
- The initial implementation must prefer an honest unavailable state over an uncalibrated probability.

### Point-in-Time Safety

For an as-of date `t`:

- every feature is computed from data dated no later than `t`;
- a training row is eligible only when its entire forward-return label is observable before `t`;
- scaler, imputation, feature selection, regression fitting, thresholds, and calibration are fit only on eligible training rows;
- ticker/date splits and joins are asserted to prevent accidental future rows;
- the forecast records the effective training cutoff and model version.

Models may pool the local universe to obtain adequate samples, but the test observation is never included in its own training data.

## Walk-Forward Evaluation

The evaluation service runs expanding-window walk-forward tests for each horizon and reports:

- sample count and coverage;
- mean absolute error and root mean squared error;
- direction accuracy, including the neutral class policy;
- rank information coefficient when the cross-section is large enough;
- quantile or signal-bucket forward returns;
- comparison with simple zero-return and historical-mean baselines;
- evaluation date range and model version.

Metrics are descriptive and displayed only when minimum sample requirements are met. The UI must distinguish model availability from evidence of useful predictive performance.

## API and Data Flow

1. The repository loads point-in-time-safe price histories and factor observations.
2. The forecast provider builds eligible training examples for each as-of date and horizon.
3. The stock-detail service returns chart data, factors, structures, scenarios, forecasts, and evaluation metadata.
4. The client indexes forecasts by date and horizon.
5. Crosshair movement updates the chart marker and detail panel locally, without issuing a request for every mouse event.

Forecast computation should be cached by database revision, ticker, as-of date range, horizon, and model version. Cache invalidation occurs after a successful market-data update.

## Error Handling

- Invalid locale values fall back to `zh-CN`.
- Missing translation keys fall back to English, then to the key in development diagnostics.
- Malformed dates render as `—` and do not reach the chart formatter.
- Forecast failures are isolated from price/factor rendering; a ticker remains inspectable when the forecast provider fails.
- Sparse history, insufficient training rows, degenerate targets, and unavailable optional model dependencies produce typed unavailable reasons.

## Testing

Automated tests cover:

- deterministic tick and full-date formatting under multiple simulated browser locales;
- locale default, switching, persistence, fallback, and accessibility state;
- complete message coverage for visible static and dynamic UI text;
- factor explanation content and keyboard/pointer behavior;
- forecast contract validation and unavailable states;
- feature/label cutoff assertions and deliberate future-data leakage traps;
- 5/20/60-session label alignment;
- deterministic walk-forward results on synthetic data;
- chart crosshair, click-lock, horizon switching, marker positioning, and missing forecasts;
- unchanged existing API and dashboard behavior.

Final verification includes the full Python and JavaScript suites plus desktop and mobile browser checks in both languages.

## Delivery Boundaries

Included: deterministic dates, bilingual UI, factor explanations, point-in-time direction signals, model diagnostics, walk-forward evaluation, tests, and documentation.

Excluded: live trading, orders, portfolio sizing, push alerts, target-price recommendations, claims of profitability, and silent automatic model promotion.
