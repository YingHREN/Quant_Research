# Task 7 Report: Walk-Forward Evaluation and Calibration Gate

## Status

Implemented and verified.

## Implementation

- Added `walk_forward_evaluate(frame, horizon, provider)`.
  - Evaluates finite realized targets in chronological order.
  - Calls the forecast provider for every candidate row and reuses
    `eligible_training_rows` for the point-in-time historical-mean baseline.
  - Defines coverage as available forecasts divided by all finite realized-target
    candidates.
  - Computes MAE, RMSE, three-class direction accuracy, zero-return MAE, and
    historical-mean MAE over the exact same available-forecast rows.
  - Computes rank IC as the mean of per-date Spearman correlations only for
    cross-sections with at least five available names and non-degenerate ranks.
  - Computes direction-signal bucket returns only from those adequate
    cross-sections. All three bucket keys are returned and empty buckets are
    explicitly `None`; all buckets are `None` below the threshold.
  - Records sample count, coverage, evaluation date range, model key/version,
    and typed unavailable evaluations.
- Added `calibrate_up_probability(predictions, actuals, minimum_samples=100)`.
  - Treats the final prediction as the current query and never uses its outcome.
  - Fits a deterministic empirical isotonic calibration with NumPy only.
  - Requires at least 100 finite earlier OOS rows and both up/non-up outcomes.
  - Returns `None` with a stable reason when either gate is unavailable.
- Integrated calibration into `RidgeForecastProvider` through an optional,
  provenance-bearing calibration history.
  - Requires OOS as-of date, matured label-end date, training cutoff, horizon,
    prediction, actual, model key, and model version.
  - Requires each training cutoff to precede its OOS prediction date.
  - Uses only matching model/horizon rows whose prediction and label end are
    strictly earlier than the live as-of date.
  - Keeps `up_probability=None` and `confidence_status="uncalibrated"` unless all
    calibration gates pass.
- Exported the public evaluation and calibration interfaces from
  `web.forecasts`.

## TDD Evidence

1. Initial focused test run failed with `ModuleNotFoundError` for
   `web.forecasts.evaluation`.
2. Evaluation and calibration implementation made the focused tests pass.
3. Added an empty-frame regression test, observed the expected invalid-`asof`
   failure, then fixed empty-frame validation.
4. Added calibration provenance rejection assertions, observed failures for
   missing provenance fields, then made the schema fail closed.

## Verification

- Focused, warning-strict:
  `PYTHONWARNINGS=error ../../venv/bin/python -m unittest tests.test_web_forecast_evaluation tests.test_web_forecasts -v`
  - 17 tests passed.
- Full, warning-strict:
  `PYTHONWARNINGS=error ../../venv/bin/python -m unittest discover -s tests -v`
  - 168 tests passed.
- Bytecode compilation:
  `PYTHONWARNINGS=error PYTHONPYCACHEPREFIX=/private/tmp/task7-pycache ../../venv/bin/python -m py_compile web/forecasts/evaluation.py web/forecasts/ridge.py web/forecasts/__init__.py tests/test_web_forecast_evaluation.py`
  - Passed.
- `git diff --check`
  - Passed.

The worktree does not contain its own `./venv`; verification used the parent
repository's existing interpreter at `../../venv/bin/python`.

## Self-Review

- Confirmed all primary and baseline errors share one observation domain.
- Confirmed calibration excludes current outcomes, same-day label completions,
  future rows, other horizons, and other model versions.
- Confirmed probability remains absent when sample or class gates fail.
- Confirmed empty or wholly unavailable evaluations carry no fabricated metrics.
- No known blockers or unresolved correctness concerns.

An independent read-only review against `fac4de9` also returned PASS with no
requirement-breaking findings.
