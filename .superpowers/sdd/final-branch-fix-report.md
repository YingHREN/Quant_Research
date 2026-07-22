# Final whole-branch fix report

Date: 2026-07-23
Base commit: `6182ff5` (`fix: guard forecasts during data updates`)

## Findings resolved

1. `eligible_training_rows` now normalizes every valid `asof` timestamp to its
   session date after removing timezone metadata. Midnight and noon therefore
   use an identical strict `label_end_date < session` boundary, and a label that
   matures on the forecast session cannot enter training.
2. First-party strict-VCP and tight-platform results now add the stable
   `rejection_reason_code` field while retaining the legacy `reject_reason` or
   `reason` prose unchanged. All currently emitted legacy Chinese reasons map
   to stable codes, including the two parameterized reason families. The
   dashboard localizes known rejection values in Simplified Chinese and English
   in the factor formatted/raw table cells, factor popover current value, and
   nested structure panel. Older payloads without a code remain compatible
   through legacy-reason extraction; unknown future prose remains visible.
3. The client date parser now accepts only an exact `YYYY-MM-DD` or a complete,
   range-checked ISO datetime suffix. Garbage suffixes and invalid hour, minute,
   second, or offset components render as an em dash.

## Strict TDD evidence

- Cutoff RED: the noon regression returned 16 rows while midnight returned 15;
  the same-session label was incorrectly eligible.
- Rejection contract RED: the real short-history API payload raised `KeyError`
  for `rejection_reason_code`; the runtime still showed
  `Rejected: 历史不足` and raw Chinese values.
- Date RED: `2026-07-17Tgarbage` and invalid times were accepted from their date
  prefix.
- Focused GREEN: the four direct regressions passed.
- Broader GREEN: 133 warning-strict dataset, provider, walk-forward evaluation,
  factor, API, and frontend tests passed.

## Final verification

- Full warning-strict Python/Node-backed suite: 201 tests in 4.522 seconds,
  `OK`.
- `node --check` passed for every `web/static/js/*.js` module and the dashboard
  runtime harness.
- `py_compile` passed for the modified Python modules/tests and Flask app.
- `git diff --check` passed.

## Remaining concerns

- No known correctness gap remains from the three final review findings.
- Unknown future legacy rejection prose intentionally falls back to its raw
  value until its producer supplies a stable code or the compatibility map is
  extended.
