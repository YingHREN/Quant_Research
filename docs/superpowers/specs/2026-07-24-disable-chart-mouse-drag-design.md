# Disable Chart Mouse-Drag Panning

## Problem

After a user clicks a historical point to lock the forecast, pressing the mouse button and moving horizontally still pans the entire price and volume timeline. This makes it difficult to inspect the forecast at the selected date and can look like the forecast overlay is dragging the layout.

The forecast overlay is not changing the visible range in this case. The chart library's default `pressedMouseMove` interaction is handling the gesture as a horizontal pan, and the synchronized price and volume charts move together.

## Chosen behavior

Disable mouse-button drag panning on both the price chart and the volume chart.

The following interactions remain available:

- Moving the pointer across dates to inspect historical values and forecasts.
- Clicking a date to lock or unlock the inspection point.
- Mouse-wheel chart navigation and zooming.
- Existing time-range buttons.
- Horizontal and vertical touch gestures.

The change applies consistently whether or not a date is locked. This avoids interaction state coupling and prevents accidental timeline movement before or after locking a forecast.

## Implementation boundary

Add an explicit `handleScroll` configuration to the shared chart options in `web/static/js/charts.js`:

- `pressedMouseMove` is disabled.
- `mouseWheel`, `horzTouchDrag`, and `vertTouchDrag` remain enabled.

No forecast calculations, factor scores, chart data, axis formatting, or prediction rendering are changed.

## Verification

Automated asset tests will verify that:

- Both created charts disable `pressedMouseMove`.
- Supported wheel and touch interactions remain enabled.
- Existing forecast projection updates continue to preserve the visible time range.

Browser verification will use the NBIS chart:

1. Select and lock a historical date.
2. Wait for its historical forecast line to appear.
3. Press and drag horizontally over the price chart.
4. Confirm the price and volume date axes do not move.
5. Confirm pointer inspection and click-to-lock behavior still work.

