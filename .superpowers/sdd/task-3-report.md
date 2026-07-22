# Task 3 Report: Linked-Chart Dates and Localized Details

## Result

Task 2 had already added deterministic `formatChartTickDate` and `formatFullDate`
formatters to the common chart options, and had localized chart detail/series text.
Task 3 adds the missing regression coverage and re-applies the explicit date
formatters to both linked charts when `setLocale(locale)` runs.

## TDD evidence

1. Added `WebAssetTest.test_chart_dates_are_deterministic`.
2. RED: the test failed before the production update because neither chart had
   received locale-change `applyOptions` date settings (`actual: undefined`).
3. GREEN: `charts.js` now reapplies `timeScale.tickMarkFormatter` and
   `localization.timeFormatter` to price and volume charts.

The regression test verifies both initial chart options and locale-change
options, validates the deterministic `07-17` tick and `2026-07-17` full date,
and checks the detail heading is exactly `2026-07-17`.

## Verification

- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_dates_are_deterministic -v`
  - PASS (1 test)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets -v`
  - PASS (29 tests)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -v`
  - PASS (124 tests)
- `git diff --check`
  - PASS

## Reviewer follow-up: preserve viewport and crosshair on locale updates

Extended the locked-row runtime harness with stateful linked time scales and
crosshair handlers. The test now loads 65 rows, selects the non-default `3m`
range, records both linked visible logical ranges and the synchronized volume
crosshair, then verifies those visual states remain unchanged across
`en → zh-CN → en` locale updates. No production defect was exposed.

### TDD evidence

The first focused run failed because the new assertion assumed an exact
logical-range start instead of recording the chart's active range. The harness
now records the runtime range (while asserting the `3m` viewport differs from
the default full range) and checks that captured state is preserved through
each `applyOptions` locale update.

### Follow-up verification

- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_locale_switch_preserves_locked_row_and_localizes_runtime_details -v`
  - PASS (1 test)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets -v`
  - PASS (30 tests)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -v`
  - PASS (125 tests)

## Review notes

The changed code is limited to the chart locale update path and its focused
runtime asset test. Existing Task 2 localization behavior and formatter wiring
remain intact. The worktree does not have `./venv`; verification used the
repository virtual environment at the absolute path above.

## Reviewer follow-up: locked-row and runtime-localization coverage

Added `WebAssetTest.test_chart_locale_switch_preserves_locked_row_and_localizes_runtime_details`.
It supplies two rows, clicks the earlier one to lock it, and switches `en → zh-CN → en`.
The test asserts the locked `2026-07-17` row remains selected, the lock instruction is
localized on each switch, detail labels are localized in both locales, and the Volume
MA20 / Volume ratio line-series titles are reapplied in both locales.

### TDD evidence

The first focused run failed because the new test expected an outdated English lock
instruction (`Locked · click chart to unlock`) while the catalog correctly provides
`Locked · click a chart to unlock`. Correcting that fixture expectation produced a
green run; no production defect or production-code change was exposed.

### Follow-up verification

- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_chart_locale_switch_preserves_locked_row_and_localizes_runtime_details -v`
  - PASS (1 test)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets -v`
  - PASS (30 tests)
- `/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -v`
  - PASS (125 tests)
- `git diff --check`
  - PASS
