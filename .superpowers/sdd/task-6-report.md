# Task 6 Report: Expanding-Window Ridge Forecast Provider

## Result

Added `RidgeForecastProvider` with the model key `ridge_direction_v1`. Each
ticker/date/horizon request selects its forecast row by the exact Task 5
`(ticker, observation_date)` key and calls Task 5's
`eligible_training_rows(frame, asof, horizon)` unchanged. Training therefore
uses only labels whose ticker-local label-end date is strictly before the
forecast date. The reported training cutoff is the latest eligible label-end
date, which records the newest information used to fit the model.

For every expanding-window fit, non-finite feature values are median-imputed,
then standardized using statistics computed only from eligible training rows.
The NumPy ridge solve uses an explicit intercept whose penalty is zero. A
deterministic least-squares fallback handles a singular system, including
explicit `alpha=0` configurations. Constant targets, insufficient samples,
missing forecast rows, and numerical model failures produce Task 5 typed
unavailable results.

The immutable version-one neutral-band policy is 5 sessions at +/-1%, 20 at
+/-2%, and 60 at +/-4%. Exact boundaries are neutral. Available results remain
honestly uncalibrated: `up_probability` is always `None` and
`confidence_status` is `uncalibrated` until Task 7 supplies out-of-sample
calibration.

Added `ForecastRegistry`, which preserves registration order and rejects a
duplicate normalized model key before mutating the registry.

## TDD evidence

1. The initial focused suite failed at import because
   `web.forecasts.registry` did not exist.
2. Minimal registry and ridge implementations made eight deterministic,
   eligibility, preprocessing, unavailable-state, band, and duplicate-key
   tests pass under `-W error`.
3. A second RED cycle exposed mutable neutral-band metadata and acceptance of
   malformed forecast-row MultiIndexes. The focused suite failed those two
   tests while the already-covered behaviors stayed green.
4. Freezing the band mapping and validating the exact Task 5 index shape,
   uniqueness, datetime type, and non-missing dates made all ten focused tests
   pass. The suite directly checks the known linear prediction, a truly
   singular `alpha=0` fit, and an unpenalized intercept under very large ridge
   regularization.

## Verification

- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task6-final-pycache ../../venv/bin/python -W error -m unittest tests.test_web_forecasts -v`
  - PASS (10 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task6-final-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (161 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task6-final-pycache ../../venv/bin/python -m py_compile web/forecasts/ridge.py web/forecasts/registry.py tests/test_web_forecasts.py`
  - PASS
- `git diff --check`
  - PASS

## Self-review

Reviewed the implementation against every Task 6 checklist item and the Task
5 contract invariants. In particular, the forecast observation is used only
for scoring; target, imputer, scaler, and fit inputs all come from the strict
eligible subset. Changing forecast-row targets, unobservable rows, or future
feature rows cannot change an earlier result. Forecast provenance stays valid
for both available and unavailable records, including positive sample counts.

No Task 5, UI, API, evaluation, or calibration code changed. Probability
calibration intentionally remains deferred to Task 7.
