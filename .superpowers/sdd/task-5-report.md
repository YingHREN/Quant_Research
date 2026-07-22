# Task 5 Report: Forecast Contracts and Point-in-Time Dataset Builder

## Result

Added immutable `ForecastResult` and `ForecastEvaluation` contracts with fresh,
JSON-safe serialization and a typed `UnavailableReason` enum containing
`insufficient_history`, `insufficient_training_samples`, `degenerate_target`,
and `model_error`.

Added pure dataset builders for the supported 5/20/60-session horizons. Feature
rows use a unique `(ticker, observation_date)` index and causal trend, momentum,
structure, volume, and risk calculations. Forward targets are aligned by each
ticker's own trading-session positions and retain an explicit
`label_end_date_{horizon}`. Training eligibility requires this end date to be
strictly before the forecast date.

Malformed duplicate keys fail closed, sparse and NaN histories retain their
rows and missing values, infinities are normalized to missing values, and input
histories are not mutated.

## TDD evidence

1. The initial target-alignment, strict eligibility-boundary, and future-spike
   leakage tests failed because `web.forecasts` did not exist.
2. The minimal dataset implementation made those three tests pass under
   `-W error` after removing a pandas downcasting warning exposed by the suite.
3. Contract, 5/20/60, sparse/NaN, duplicate-key, missing-label, and invalid-
   horizon tests then failed because `web.forecasts.base` did not exist.
4. The immutable contracts and validation made the expanded suite pass except
   for a warning-strict sparse-history test. That test exposed the existing
   chart helper's deprecated missing-value fill behavior.
5. Computing the causal indicator series directly with the shared canonical
   `_ema`, `_sma`, `vcp_analysis`, and `tight_platform` primitives eliminated
   that warning; all 11 focused tests then passed.

## Verification

- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-review-pycache ../../venv/bin/python -W error -m unittest tests.test_web_forecast_dataset -v`
  - PASS (14 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-review-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (144 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-pycache ../../venv/bin/python -m py_compile web/forecasts/__init__.py web/forecasts/base.py web/forecasts/dataset.py tests/test_web_forecast_dataset.py`
  - PASS
- `git diff --check`
  - PASS

## Self-review

Reviewed the session-vs-calendar alignment, strict `< asof` purge boundary,
per-ticker grouping, no cross-ticker target bleed, duplicate rejection, sparse
history behavior, source-frame immutability, non-finite serialization, nested
mapping immutability, and post-cutoff feature leakage. No UI files changed.

The structure features intentionally use the canonical VCP and tight-platform
implementations once 60 sessions are available; this is more computationally
expensive than the vectorized features but keeps their definitions consistent
with the registered dashboard factors. Model fitting, minimum training sample
policy, and error isolation remain Task 6 responsibilities.

## Independent review fixes

The completion review found three downstream-safety gaps. New RED tests first
proved that incoherent result states, unsupported scalar values, `NaT` dates,
and malformed label metadata were accepted.

The contracts now normalize all optional numeric/date values at construction,
reject unsupported values, deeply copy and validate signal-bucket values, and
enforce coherent availability/calibration states. `json.dumps(...,
allow_nan=False)` is exercised directly. Eligibility now requires datetime
keys and label-end values, asserts each label end follows its observation and
matches the ticker-local horizon shift, and only then applies the strict
`label_end < asof` cutoff. Horizon types are consistently integral across the
dataset and result contracts.

The independent re-review reported no remaining Critical, Important, or Minor
findings and marked the dataset contracts ready for Task 6.

## Reviewer follow-up fixes

A later Task 5 review identified five additional downstream-contract gaps. New
regressions first reproduced all five under `-W error`: available forecasts
accepted missing or non-causal training provenance; evaluations accepted mixed
available/unavailable states and out-of-domain metrics; structure features
turned a NaN 100 sessions back into a false negative; mutable model/ticker
identity inputs were stringified only during serialization; and the empty
builder returned an object-typed observation-date level rejected by target
attachment.

The result contract now requires available forecasts to have a valid as-of
date, a positive training sample count, and a valid training cutoff strictly
before the as-of date while preserving prediction-free typed unavailable
states. Ticker and model identities are snapshotted as trimmed, non-empty
scalar strings at construction and mutable inputs are rejected.

The evaluation contract now distinguishes availability through
`unavailable_reason`: available evaluations require positive samples, complete
core metrics, a valid date range, and model identity; unavailable evaluations
must omit metrics and retain a typed reason. Error metrics are nonnegative,
coverage and direction accuracy are in `[0, 1]`, rank IC is in `[-1, 1]`, and
signed signal-bucket returns remain unrestricted apart from being finite when
present. Nonzero unavailable sample counts may retain their valid observed date
range without exposing suppressed metrics.

Structure features now inspect the complete canonical dependency window of up
to 252 sessions and remain missing when any required OHLCV value in that window
is missing. Empty feature frames now carry a datetime-typed observation-date
index and can flow directly through `attach_forward_targets`.

Follow-up TDD and verification evidence:

- Focused RED command: five new focused tests failed with 28 expected subtest
  failures before production changes.
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-review-green-pycache ../../venv/bin/python -W error -m unittest tests.test_web_forecast_dataset -v`
  - PASS (20 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-final-full-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (150 tests)

The first independent follow-up review found one remaining Important edge case:
an unavailable result could be prediction-free while carrying a non-causal
cutoff or a positive sample count without a cutoff. It also noted that Python
booleans passed the real-number check. Additional RED tests reproduced both.
All supplied result cutoffs now require an as-of date and precede it strictly,
positive training counts require a cutoff in available and unavailable states,
and numeric contract fields reject booleans. The final focused and full counts
above include those review regressions.

## Final Important findings

The final review required a valid `asof_date` for every `ForecastResult`, not
only available predictions, and required the structure dependency guard to
reject every non-finite OHLCV value rather than NaN alone. RED coverage showed
that unavailable results accepted both `None` and `NaT` as-of dates. It also
showed that `+inf` and `-inf` placed 100 sessions back in each of Open, High,
Low, Close, and Volume reached the canonical structure detectors and produced
false `0.0` signals (and, for some close paths, numeric warnings).

Construction now rejects a missing as-of date before branching on forecast
availability. The structure guard now applies `numpy.isfinite` to the complete
up-to-252-session OHLCV matrix before either detector runs, leaving
`strict_vcp` and `tight_platform` missing for NaN, positive infinity, or
negative infinity in any required input.

Final verification:

- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-final2-green-pycache ../../venv/bin/python -W error -m unittest tests.test_web_forecast_dataset -v`
  - PASS (21 tests)
- `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-task5-final2-full-pycache ../../venv/bin/python -W error -m unittest discover -s tests -v`
  - PASS (151 tests)
