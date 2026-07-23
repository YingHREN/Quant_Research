# Causal Reversal Factors and Historical Chart Design

## Goal

Add point-in-time definitions for prior-high breakout, descending-trendline
breakout, and confirmed higher low; expose them for regression research and
show their state at every chart date without using future observations.

## Definitions

### Prior-high breakout

- Resistance is the highest closing price in the prior 20 sessions, excluding
  the observation session.
- `prior_high_breakout` is true only on a crossing session: the previous close
  is at or below its then-known resistance and the current close is above the
  current resistance.
- `prior_high_breakout_pct` is current close divided by resistance minus one,
  in percent.

### Confirmed swing points

- Swing candidates are generated sequentially from close prices using an
  ATR-scaled ZigZag reversal threshold clamped to 3%-10%.
- A pivot becomes usable only on the session whose price movement confirms the
  reversal. The pivot's original date and its later `confirmed_date` are both
  retained.
- No output for date `t` may use a pivot whose `confirmed_date` is after `t`.

### Descending-trendline breakout

- Use the latest two confirmed swing highs known by the observation date.
- The two highs must have decreasing prices and increasing pivot dates.
- Extend the line through those highs to the observation date.
- `trendline_breakout` is true only when the previous close is at or below the
  prior session's known line and the current close is above the current line.
- Return the line value and the two source pivot dates for auditability.

### Confirmed higher low

- Use the latest two confirmed swing lows known by the observation date.
- The latest low must exceed the previous low by at least `0.25 * ATR20` as
  measured on the latest low's confirmation date.
- The event is emitted on the latest low's confirmation date, not retroactively
  on the pivot date.
- Return both pivot dates, prices, and the confirmation date.

### Composite candidate

- `reversal_signal_count` is the number of the three event flags true on the
  observation date.
- `reversal_candidate` is true when at least two event flags are true.
- The three raw components remain separate model inputs; the composite is a
  chart/research convenience, not a validated trading recommendation.

## Interfaces

- Create `research/reversal.py` for causal swing confirmation and row-wise
  reversal feature construction.
- `build_reversal_rows(history)` returns one mapping per input session with
  resistance, trendline, higher-low, component flags, and audit metadata.
- `web.factors.builtin.build_chart_rows()` merges these mappings into every
  OHLCV chart row.
- Register the three component factors and composite count in the dashboard
  factor registry.
- The stock API keeps its existing schema and gains fields inside each chart
  row. No new remote data access is allowed.

## Chart behaviour

- Draw the point-in-time descending resistance line as a line series with
  whitespace gaps when no valid line exists.
- Mark each component event and composite candidate on its actual detection
  session.
- Hover/click details show resistance, trendline level, three event states,
  signal count, and source/confirmation dates for the selected session.
- Existing forecast markers and range controls continue to work.
- Preserve at least 20px between price and volume chart containers so date
  labels are not covered.

## Validation

- Unit tests prove confirmation lag and prove appending future rows cannot
  change earlier feature rows.
- API tests prove every field is JSON-safe and present on historical dates.
- JavaScript tests prove line data, event markers, and hover values use payload
  data rather than recomputing patterns in the browser.
- The factors enter later 5/20/60-session walk-forward regression unchanged;
  this implementation does not claim predictive validity.

