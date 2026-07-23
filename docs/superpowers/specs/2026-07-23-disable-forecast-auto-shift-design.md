# Disable Forecast Auto-Shift Design

## Problem

Hovering a historical date replaces the forecast projection series with points
that can extend beyond the latest market observation. Lightweight Charts has
`shiftVisibleRangeOnNewBar` enabled by default, so adding those future points
can move the visible time range to the right. Because the price and volume
charts synchronize their logical ranges, the movement can propagate to both
charts.

The existing code snapshots and restores the logical range around
`forecastProjectionSeries.setData()`. That protects against synchronous range
changes, but it does not prevent the chart library's own new-bar shifting
behavior from running during the data update.

## Required Behavior

- Moving the pointer across dates must not move candles, volume bars, or date
  labels.
- Hovering must update only the forecast path, forecast marker, and detail
  content.
- Price and volume charts must keep exactly the same visible logical ranges.
- A future forecast outside the current viewport may be clipped.
- Manual zooming, scrolling, range buttons, and price/volume synchronization
  must continue to work.

## Selected Design

Set `timeScale.shiftVisibleRangeOnNewBar` to `false` in the shared chart
options. The option therefore applies to both the price chart and the linked
volume chart.

Keep the existing invisible timeline anchor and explicit logical-range
preservation. They protect date-index stability and provide compatibility
across chart-library versions, while the new option prevents the source
auto-shift from occurring.

Do not reset the selected range during hover and do not expand the viewport to
show a forecast target. Forecast rendering remains subordinate to the user's
current chart position.

## Testing

Extend the chart interaction regression test so the fake chart models
Lightweight Charts' default new-bar shift:

1. when `shiftVisibleRangeOnNewBar` is not disabled, adding future forecast
   points moves the range;
2. with the selected design, repeated forecast updates leave both linked
   logical ranges unchanged;
3. range buttons and forecast-horizon switching still preserve their existing
   behavior.

After the automated suite passes, verify on the live NBIS one-year chart by
moving across multiple historical dates and waiting for forecasts to load.
Candles, volume bars, and the date axis must remain fixed while the forecast
path changes.

## Scope

This change does not modify forecast values, model factors, trend evidence,
visual styling, or market data.
