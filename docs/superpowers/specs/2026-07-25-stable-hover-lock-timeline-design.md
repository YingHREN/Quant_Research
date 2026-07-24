# Stable Hover and Lock Timeline Design

## Goal

Keep the NBIS price and volume timelines visually stable while historical
forecasts load. Hovering must remain continuous through the right side of the
visible chart, and locking a date must freeze that date and both linked
crosshairs until the user explicitly unlocks it.

## Root Cause

Historical forecast responses currently append their target and projection
dates to a transparent `timelineAnchorSeries`. Those new dates change the
chart's logical time index after the pointer has already selected a candle.
The selected candle can therefore move toward the right boundary, and a locked
crosshair can appear to drag with the chart even though mouse-drag panning is
disabled.

The existing regression test covers forecasts already present in the initial
payload. It does not cover an asynchronous forecast response that introduces
previously unseen future dates.

## Chosen Design

Forecast interaction must be time-axis neutral:

- Historical forecast responses may update the forecast marker and the detail
  panel.
- They must not add target dates, projection dates, whitespace points, or any
  other data to a chart series.
- The hidden forecast projection series remains empty while forecast lines are
  disabled.
- The transparent timeline anchor series and its cumulative date state are
  removed.
- Price rows remain the sole owner of the linked charts' logical time index.

Mouse-drag panning remains disabled in both locked and unlocked states. Mouse
wheel and touch behavior are unchanged.

## Interaction Contract

### Unlocked

- Moving across 2026-06-22, 2026-06-24, and later candles updates the selected
  row and detail panel continuously.
- Loading a historical forecast does not change the visible logical range,
  shared chart dates, or candle positions.

### Locked

- Clicking 2026-06-30 locks the price crosshair, volume crosshair, displayed
  date, and forecast marker to 2026-06-30.
- Pointer movement and asynchronous forecast completion cannot change the
  locked date or either crosshair.
- A subsequent chart click explicitly unlocks the selection.

## Verification

Add regression coverage that:

1. Starts with no forecast for a hovered historical date, resolves an
   asynchronous forecast containing new future projection dates, and verifies
   that the chart's shared dates and visible range remain unchanged.
2. Repeats the asynchronous completion while a historical date is locked and
   verifies that the locked date, both crosshairs, and visible range remain
   unchanged.
3. Runs the complete automated test suite.
4. Repeats the NBIS browser interaction at 2026-06-22/24 and 2026-06-30.

## Non-Goals

- Re-enabling forecast trend lines.
- Re-enabling desktop mouse-drag panning.
- Changing forecast model calculations, factor scores, or market data.
