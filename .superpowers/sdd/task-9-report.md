# Task 9 Report: Crosshair Forecast Direction and Horizon Controls

## Status

Implemented and verified.

## Implementation

- Added `web/static/js/forecasts.js`.
  - Indexes the stock forecast bundle once into sparse date/horizon maps.
  - Exposes `indexForecasts(payload)` and both implicit-current and explicit-index
    forms of `forecastFor(...)`.
  - Restricts horizons to exactly 5, 20, and 60 sessions, with 20 as the chart
    default.
  - Renders localized signal fields and walk-forward evidence without using
    dynamic HTML strings.
  - Shows an up probability only when the payload contains a finite calibrated
    value; otherwise it displays the localized confidence reason.
  - Uses an honest `暂不可评估` / `Unavailable` state for sparse or unavailable
    observations.
- Integrated forecasts with the linked chart controller.
  - Added `setForecasts(payload)`, `setForecastHorizon(horizon)`, and
    `getForecastHorizon()`.
  - Crosshair movement performs only local map lookup and DOM/chart updates; the
    chart and forecast modules contain no network request path.
  - Click lock freezes the OHLCV detail and its forecast marker together.
  - Locale and range changes preserve the selected horizon and locked date.
  - One `createSeriesMarkers` controller now owns existing shape annotations and
    at most one current forecast marker. Every update replaces its sorted marker
    array, so cursor movement cannot accumulate primitives or markers.
  - Direction remains readable without color: localized marker text accompanies
    green up-arrow, gray circle, and red down-arrow shapes.
- Added accessible, responsive 5/20/60 horizon controls to the chart panel.
  - The control is an ARIA-labeled button group with pressed state.
  - Controls wrap on narrow screens, use mobile-sized targets, and evidence/value
    grids collapse to one zero-minimum-width column.
  - The research disclaimer remains statically visible and is repeated beside
    the selected point-in-time signal.
- Added complete Chinese and English forecast labels to the central localization
  catalog.
- Added walk-forward evidence beside the signal: coverage, direction accuracy,
  MAE, zero-return and historical-mean baseline comparison, evaluation sample
  count and period, and model version.

## Strict TDD Evidence

1. The first focused interaction test failed because
   `web/static/js/forecasts.js` did not exist.
2. The focused test passed after the minimal forecast index, renderer, chart
   overlay, and controls were implemented.
3. The first warning-strict asset run exposed legacy DOM harness nodes without
   `setAttribute`; the renderer was made compatible while retaining the ARIA
   label in real DOM implementations.
4. The asset suite then exposed that two marker primitives changed existing
   annotation-controller ordering. Consolidating annotations and the one current
   forecast into one replaceable, chronologically sorted marker array fixed the
   lifecycle regression.
5. Additional RED/GREEN assertions covered direct forecast-subpayload indexing,
   sorting a historical cursor marker before a later annotation, and valid CSS
   design-token/segmented-control styling.

## Verification

- Focused interaction, warning-strict:
  `../../venv/bin/python -W error -m unittest tests.test_web_assets.WebAssetTest.test_chart_forecast_interaction -v`
  - `Ran 1 test` / `OK`.
- Complete asset suite, warning-strict:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task9-assets-pycache ../../venv/bin/python -m unittest tests.test_web_assets -v`
  - `Ran 33 tests` / `OK`.
- Full suite, warning-strict:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task9-final-full-pycache ../../venv/bin/python -m unittest discover -s tests -v`
  - `Ran 185 tests` / `OK`.
- JavaScript syntax checks:
  `for file in web/static/js/*.js tests/dashboard_runtime.mjs; do node --check "$file" || exit 1; done`
  - Exit 0, no output.
- `git diff --check`
  - Exit 0, no output.

The worktree has no local `./venv`; verification used the shared parent
repository interpreter at `../../venv/bin/python` (shown above in its equivalent
relative form).

## Self-Review

- Confirmed exact 5/20/60 controls and default 20; unsupported horizons preserve
  the previous valid selection.
- Confirmed sparse date lookup returns `null`, clears only the forecast marker,
  preserves structural annotations, and renders `暂不可评估` / `Unavailable`.
- Confirmed calibrated and uncalibrated confidence paths are mutually exclusive,
  and no probability is synthesized.
- Confirmed predicted return, training sample count/cutoff, model identity,
  walk-forward evidence, baseline values, sample period, and disclaimer are all
  visible and localized.
- Confirmed click lock, locale switching, and range changes retain a synchronized
  date/horizon signal.
- Confirmed marker text communicates direction independently of color and the
  mobile layout has no fixed-width forecast content.
- Confirmed crosshair handlers do not call `fetch` or any API helper.

## Remaining Concerns

- No known Task 9 correctness blocker remains.
- Automated DOM/chart tests cover responsive CSS contracts and accessibility
  state, but Task 10 still owns final interactive desktop/mobile browser QA.
