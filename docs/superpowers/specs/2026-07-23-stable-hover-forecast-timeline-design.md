# Stable Hover Forecast Timeline Design

## Problem

The price chart rewrites the model forecast series whenever the selected or
hovered date changes. Lightweight Charts builds one shared time-point index
from every series. Removing one forecast path and adding another therefore
changes that index even when the visible logical range is restored.

The defect is observable on NBIS: after locking 2025-10-20 and loading its
forecast, the vertical crosshair remains in place while the time-axis label
under it changes to 2025-12-09. Moving the pointer then makes the candles appear
to follow the pointer.

## Desired Behavior

- Hovering or locking a date must not move the candles or change the visible
  date range.
- The crosshair date, detail-panel date, and candle under the crosshair must
  remain identical.
- Loading a historical forecast must only change the forecast line, marker, and
  detail content.
- Switching between 5-, 20-, and 60-session forecasts must preserve zoom,
  scroll position, and a locked crosshair.
- Price and volume charts must remain synchronized.

## Selected Design

Add an invisible timeline-anchor line series to the price chart.

When stock data is loaded, populate the anchor once with whitespace points for:

1. every observed chart date; and
2. every future session exposed by the latest forecast payload, using the
   longest available projection calendar.

Historical forecast targets cannot extend beyond the latest observation plus
the maximum supported horizon. Consequently, all dates later used by a hover
forecast already exist in the shared chart time index. Replacing forecast line
values will no longer add or remove time points.

The anchor series will have no visible line, price line, last-value label, or
crosshair marker. It is an implementation detail and must not appear in the
legend.

The existing logical-range preservation around forecast `setData()` remains as
defensive compatibility, but correctness must no longer depend on it.

## Data Flow

1. `setChartData(payload)` receives chart rows and the latest precomputed
   forecasts.
2. A pure helper creates sorted, duplicate-free anchor dates from chart rows
   and all projection dates present in the payload.
3. The invisible anchor series receives those whitespace points before the
   selected range is applied.
4. Hovering selects a row and may fetch its point-in-time forecast.
5. The visible forecast series is replaced, but every forecast date is already
   represented by the anchor series, so the time index remains stable.

If a fetched forecast unexpectedly includes a date outside the anchor, the
anchor may be extended monotonically. Existing anchor dates must never be
removed during hover interaction.

## Testing

Add a regression test around the chart adapter that models Lightweight Charts'
shared time index:

- loading a forecast path must not change the visible date mapping;
- moving from one historical forecast to another must not shift the selected
  candle;
- an asynchronously fetched forecast may extend the anchor but may not remove
  existing dates;
- horizon switching preserves the visible logical range and locked crosshair;
- the anchor series has invisible presentation options.

After automated tests pass, verify in the live dashboard with NBIS:

1. select the one-year range and 20-session horizon;
2. lock 2025-10-20 and wait for the downward forecast;
3. confirm the axis and detail panel both remain on 2025-10-20;
4. unlock and move the pointer right across multiple dates;
5. confirm the candles, axis labels, and volume bars stay fixed.

## Scope

This change fixes time-axis stability only. It does not alter forecast values,
forecast thresholds, trend evidence, chart styling, or market data.
