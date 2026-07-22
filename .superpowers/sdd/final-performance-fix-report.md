# Task 10 final performance-fix report

Date: 2026-07-23
Base commit: `5878ef6` (`docs: explain localized forecast workstation`)

## Status

The four Important findings from the Task 10 review are resolved. The real
loopback AAPL endpoint now returns in less than five seconds cold and retains an
honest, explicitly sparse forecast/evaluation contract. Browser QA was not run,
per the final-fix instruction.

## Architecture and tradeoffs

### Bounded synchronous forecasts

The request path no longer performs three exhaustive walk-forward evaluations
or fits every chart date. Production computes the latest requested chart date
for 5/20/60 sessions. `forecasts.date_coverage` makes that budget explicit:

```json
{
  "requested_date_count": 501,
  "computed_date_count": 1,
  "computed_dates": ["2026-07-21"],
  "policy": "latest_only_synchronous",
  "omitted_reason": "not_precomputed"
}
```

Older dates are not backfilled with a current model. The UI renders their typed
`not_precomputed` reason. Every horizon retains the complete evaluation schema,
but production reports zero samples, null metrics, and
`unavailable_reason: not_precomputed` until exhaustive revision/model evidence
is generated offline and integrated through a separate ingestion path. The
offline command does not populate the production API automatically. Partial
evaluation is never presented as full evidence.

### Revision artifacts and causal feature performance

One feature/target frame and provider are cached per database revision and
shared by distinct stock/range bundle keys. Invalidation clears both the
revision artifacts and the bounded exact-response cache.

The forecast dataset still produces the same point-in-time strict-VCP and
tight-platform booleans, but computes their gates with rolling/NumPy arrays
instead of rebuilding a pandas prefix and rich diagnostic dictionary for every
row. A saved pre-change real frame and the optimized frame matched on all
90,040 rows for both columns (zero mismatches). The live provider may skip
revalidating label alignment only for a frame freshly produced by
`attach_forward_targets`; externally constructed provider frames retain full
validation. The canonical `label_end_date < asof` mask is unchanged.

### Write-aware invalidation

Each update run remembers its starting write count. If that run commits any
price write, forecast invalidation completes while the public state remains
`running`, before `completed`, `partial`, `rate_limited`, or `failed` is
published. A resumed run invalidates again if it commits new writes. Completed,
partial, rate-limited, and failed zero-write runs retain the cache. Invalidation
failure keeps the existing typed `failed/cache_invalidation_error` behavior.

### Probability event

Calibration now receives the horizon and classifies each matured realized
return through the same neutral-band policy as direction. A positive return
inside +1%/+2%/+4% for 5/20/60 sessions is non-up, not up. The 100-row,
both-class, earlier-OOS maturity gates are unchanged.

### Documentation tests

The token-presence assertions in `tests/test_docs.py` were removed. They had
encoded the two incorrect behaviors instead of proving them. Behavioral tests
now exercise mixed-write invalidation, zero-write retention, resumption,
positive-but-neutral calibration, sparse date/evaluation contracts, UI reason
rendering, causal structure parity, and large-universe fitting. Both operating
guides describe the actual behavior.

## Profile-guided evidence

Real database: 181 tickers, 90,040 rows.

Baseline boundary command timed repository snapshot, `build_feature_frame`, and
`attach_forward_targets` separately:

- snapshot: 0.169 s;
- features: 44.129 s;
- targets: 0.739 s.

Baseline cProfile (`/private/tmp/task10-features.prof`):

- 300,171,012 calls in 76.781 instrumented seconds;
- `_structure_features`: 75.592 s cumulative;
- `tight_platform`: 79,361 calls / 45.758 s cumulative;
- `vcp_analysis`: 79,361 calls / 21.208 s cumulative;
- `_atr`: 103,251 calls / 56.198 s cumulative.

Point-fit scaling on the saved real frame was 0.346 s for one chart date/three
horizons, 3.232 s for 10 dates, and 16.306 s for 50 dates. This confirmed that
full chart-date fits and roughly 254,735 evaluation refits could not remain on
the request path.

After optimization:

- features: 1.504 s;
- targets: 0.019 s;
- strict-VCP real parity: 90,040/90,040 rows;
- tight-platform real parity: 90,040/90,040 rows.

## Strict TDD evidence

1. Performance RED: the new focused run produced three failures and one error:
   no fast causal structure implementation, default evaluation crashed through
   the request path, revision artifacts rebuilt three/five times, and the
   cache/provider count remained five. GREEN: 30 dataset/service tests passed.
2. Coverage RED: `date_coverage` raised `KeyError`. GREEN: latest-only policy,
   counts, and typed omission were serialized and tested.
3. Update RED: partial and rate-limited mixed-write branches produced three
   callback assertion failures. GREEN: all 16 update tests passed, including
   invalidation-before-terminal and resume behavior.
4. Calibration RED: the positive-but-neutral test failed because calibration
   accepted no horizon. GREEN: the evaluation/forecast suites passed with the
   band-consistent event.
5. Large-universe RED: a 90,681-row finite fit returned `model_error`; under
   warning-strict execution Accelerate surfaced a `divide by zero encountered
   in matmul` status flag. GREEN: an algebraically identical explicit RHS
   reduction returns an available finite forecast.
6. UI RED: typed `not_precomputed` evaluation still rendered generic
   `Unavailable`. GREEN: bilingual typed evidence and omitted-date reasons pass
   the Node DOM harness.
7. Numeric-warning RED: flat histories raised `invalid value encountered in
   divide`. GREEN: guarded `np.divide` is warning-clean.

## Independent final review

An independent review found no Critical issues and four Important edge cases.
Three resulted in additional regression tests and fixes:

- a short or stale ticker can no longer seed a permanently truncated
  revision-wide artifact; a later richer snapshot invalidates exact bundles and
  rebuilds the revision artifacts;
- the Flask route binds its market-data snapshot to the forecast revision it
  observed before loading, so an update cannot publish an older snapshot under
  a newer revision cache key;
- the UI receives exact `computed_dates`, allowing a computed-but-unavailable
  latest date to render the model's reason while genuinely omitted dates render
  `not_precomputed`.

The fourth finding concerned callback failure after writes. The production
invalidation method advances the revision before clearing any old cache entry.
Consequently even an exceptional clear leaves the old exact bundles unreachable
under the new revision, and the old artifact revision cannot be reused. The job
continues to publish the typed `failed/cache_invalidation_error` state rather
than claiming success. No behavior change was needed for this case.

## Real HTTP measurement

The dashboard was started on `127.0.0.1:5000`; two real curl requests wrote
their bodies under `/private/tmp`:

```text
cold: http_code=200 time_starttransfer=4.818618 time_total=4.819518 size_download=359440
warm: http_code=200 time_starttransfer=3.220220 time_total=3.220995 size_download=359440
```

Both bodies had SHA-256
`2cdd0210a76b7558f01bd9718b32191dbec4c49355e09621d885d4b83fa7e807`.
The latest model was available; all three evaluation rows were typed
`not_precomputed`. Date coverage reported 501 requested dates, exactly one
computed date (`2026-07-21`), and the `latest_only_synchronous` policy.

## Final verification

All checks were rerun after the independent-review fixes:

- warning-strict Python suite: 196 tests in 4.409 seconds, `OK`;
- `node --check` for every `web/static/js/*.js` file: passed;
- `py_compile` for the Flask app, contracts, services, factors, and forecasts:
  passed;
- `git diff --check`: passed;
- real loopback cold/warm requests: HTTP 200, byte-identical payloads, both
  timings reported above.

Interactive browser QA was not run, per instruction.

## Remaining concerns

- Historical chart forecasts are intentionally unavailable until a separate
  offline precomputation/ingestion path is implemented; only the latest chart
  date is synchronous.
- The offline exhaustive evaluator remains computationally expensive. It is
  preserved for honest research reproduction, not silently approximated.
- Warm latency is about 3.22 seconds because the non-forecast selected/peer
  factor route still recomputes diagnostics; this wave did not change that
  separate contract.
- Revision artifacts are in-memory and rebuild after process restart.
- Cache invalidation occurs before terminal publication, as required; requests
  made while an update is still running may observe the prior completed
  revision until that terminal barrier.
- Interactive desktop/mobile browser QA was deliberately not performed.
