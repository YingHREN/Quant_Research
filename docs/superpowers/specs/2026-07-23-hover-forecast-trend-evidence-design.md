# Historical Hover Forecast and Trend Evidence Design

## Goal

Make every hovered or locked trading date easy to interpret:

1. date labels remain fully visible;
2. the model forecast visibly starts at the selected candle and reaches its
   trading-session target;
3. the detail panel explains which observable conditions would strengthen an
   upward move or accelerate a downward move.

The feature remains descriptive research tooling. It must not present a
forecast or a condition list as investment advice or a guarantee.

## Chosen UI

The price chart stays visually focused. It shows:

- a prominent forecast start point on the selected candle;
- one continuous model forecast line;
- a forecast endpoint and localized series label.

The existing date detail area gains a two-column **trend evidence** section:

- **Uptrend strengthening**
- **Downtrend acceleration**

Each condition has one of three states:

- met;
- near;
- not met.

The section updates with the hovered or locked date. On narrow screens the two
columns stack vertically.

## Date-Axis Layout

The price and volume chart containers must reserve a bottom date gutter instead
of relying on a canvas that touches an `overflow: hidden` boundary.

The chart adapter will configure sufficient time-scale bottom space and the CSS
containers will include an explicit axis-safe inset. Price and volume panels
must keep their axes aligned. Regression tests will assert the gutter contract
and browser screenshots will verify that the entire `YYYY-MM-DD` crosshair
label and regular tick labels remain visible.

## Historical Forecast Visibility

The selected observation date is the forecast anchor.

When the pointer selects a date:

1. use a cached point-in-time forecast when available;
2. otherwise request that exact date from the historical forecast endpoint;
3. keep a visible loading state tied to that date;
4. ignore responses belonging to a stale ticker, date, or request generation;
5. draw the line only when the returned `asof_date` matches the selected date.

The line contains a value for every server-supplied projected trading session.
Values are a straight interpolation from the selected close to the predicted
endpoint. This is an endpoint direction guide, not a predicted daily path.

To make shallow predictions visible:

- the line is thicker than moving averages;
- a distinct start marker is always placed at the selected candle;
- the start marker and line share the forecast color;
- unavailable/loading forecasts clear any stale line from another date.

Click-to-lock freezes the selected date, line, detail data, and evidence until
the user unlocks it. Hovering while locked cannot replace them.

## Trend Evidence Model

Trend evidence is derived entirely from fields already calculated causally for
the selected chart row. No future observations are used.

### Uptrend-strengthening conditions

Ordered by structural importance:

1. **Prior-high breakout** — met when `prior_high_breakout` is true; near when
   close is within a small ATR-scaled distance below `prior_high_resistance`.
2. **Descending-trendline breakout** — met when `trendline_breakout` is true;
   near when close is just below `descending_trendline`.
3. **Higher low confirmed** — met when `higher_low_confirmed` is true.
4. **Trend support** — met when close is above both EMA20 and SMA50; near when
   it is above one of them.
5. **Volume confirmation** — met when an up day has `volume_ratio >= 1.2`;
   near when `volume_ratio >= 1.0`.
6. **Positive momentum** — use the point-in-time momentum factor values supplied
   for the selected date when available; otherwise report unavailable rather
   than substituting the latest factor value.

### Downtrend-acceleration conditions

1. **Support loss** — met when close is below both EMA20 and SMA50; near when it
   is below one of them.
2. **Lower-low risk** — compare close with the latest causally confirmed swing
   low or platform support when that level exists.
3. **Distribution volume** — met when a down day has `volume_ratio >= 1.2`;
   near when `volume_ratio >= 1.0`.
4. **Volatility expansion** — met when true range exceeds the selected date's
   ATR-based threshold.
5. **Failed breakout** — met when price falls back below prior-high resistance
   shortly after a causal breakout signal, using only rows up to the selected
   date.
6. **Negative momentum** — use point-in-time momentum evidence when available;
   otherwise show unavailable.

Conditions that lack a causal input are displayed as unavailable and do not
count as met or not met.

## Data Contract

Add a pure client-side `trendEvidence(row, context)` function. It returns:

```text
{
  upward: [{ key, state, label, evidence, threshold }],
  downward: [{ key, state, label, evidence, threshold }]
}
```

`state` is `met`, `near`, `not_met`, or `unavailable`.

The chart controller passes the selected row and only date-matched auxiliary
data to the forecast detail renderer. The renderer does not calculate trading
logic; it only localizes and displays the returned evidence.

If swing-low/platform support is not yet present in chart rows, add it to the
causal factor output as a nullable field. Do not reconstruct it from future
rows in JavaScript.

## Error and Loading Behaviour

- While a historical forecast is loading, show “Calculating forecast for
  YYYY-MM-DD” and no line from a prior date.
- A failed request keeps OHLCV and trend evidence usable and shows forecast
  unavailable.
- Changing ticker, range, language, or forecast horizon cannot apply an old
  response.
- Missing trend inputs appear as unavailable without console errors.

## Localization and Accessibility

All labels, condition states, evidence text, loading text, and threshold text
are added to both Chinese and English dictionaries.

The evidence section uses semantic headings and lists. Color is supplemental:
each state includes localized text. The forecast start marker has a concise
localized label and the detail panel remains the full textual source.

## Testing

### Unit and adapter tests

- date-axis gutter configuration is present for price and volume;
- a historical response is applied only to its selected date;
- loading clears a stale projection;
- forecast data begins at the selected row and contains every projected
  session;
- locking survives crosshair moves and horizon changes;
- each trend condition covers met, near, not-met, and unavailable cases;
- momentum evidence is never borrowed from a later date;
- Chinese and English rendering remain complete.

### Browser verification

- reproduce the supplied wide-screen dimensions;
- verify full tick and crosshair dates are visible;
- hover at least three non-latest dates and confirm line start, endpoint, and
  detail date agree;
- lock a historical date and switch 5/20/60 sessions;
- verify upward/downward evidence changes with the selected date;
- repeat at a narrow viewport and check for horizontal overflow;
- confirm no console errors.

## Out of Scope

- intraday forecasts;
- probabilistic daily path simulation;
- trade entry, stop-loss, or position-size instructions;
- hiding historical reversal signals solely to reduce chart density;
- recomputing momentum from future data in the browser.
