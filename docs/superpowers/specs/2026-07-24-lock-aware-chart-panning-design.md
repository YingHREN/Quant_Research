# Lock-Aware Chart Panning

## Problem

Mouse panning is currently disabled globally. This prevented accidental chart movement, but it also means the chart cannot be intentionally dragged. Separately, clicking a date locks the inspection point, so subsequent pointer movement no longer changes the displayed date. Together these behaviors can feel like the chart has become stuck.

## Chosen interaction

Chart panning depends on the date-lock state:

- **Unlocked:** Pressing and dragging horizontally pans both synchronized charts. Hover inspection continues to follow the pointer.
- **Locked:** Clicking a date fixes the inspection point and disables pressed-mouse panning.
- **Unlock:** Clicking either chart again clears the lock and immediately restores pressed-mouse panning.

The price and volume charts always share the same interaction mode.

## Visual feedback

The chart containers expose their current state through a `data-pan-locked` attribute:

- `false` uses a grab cursor and a grabbing cursor while pressed.
- `true` uses a crosshair cursor.

The existing detail text continues to state whether the date is locked and how to unlock it.

## Implementation

The shared initial chart option enables `handleScroll.pressedMouseMove`.

A local interaction-state helper inside `createLinkedCharts` will:

1. Apply the matching `pressedMouseMove` value to both charts.
2. Update `data-pan-locked` on both chart containers.

The helper is called when chart data resets, when a date becomes locked, and when the date is unlocked. Forecast calculation and rendering are unchanged.

## Verification

Automated tests will verify:

- Both charts start with pressed-mouse panning enabled.
- Clicking a date disables panning on both charts and marks both containers locked.
- Clicking again enables panning on both charts and clears the locked state.
- Loading new chart data restores the unlocked state.
- Existing date-lock, forecast, synchronization, and hidden-projection behavior remains intact.

Browser verification will use NBIS to confirm that an unlocked chart can be dragged, a locked chart remains fixed, and a second click restores dragging.

