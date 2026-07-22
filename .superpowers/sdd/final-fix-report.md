# Quant dashboard final-fix report

Date: 2026-07-22

## Scope and commits

- Reviewed base: `48dbcf3aaa23da1d031a91e22c7c310473ab6ba9`
- Fix commit: `15060517913df544d22799203321ddabec48d070`
- Route-optimization follow-up: `b8a7fbc76ef5794b910a7afcf709fe38e56eb21d`
- Snapshot edge-case follow-up: `4e1e147fab7e917f45bf10502284758feaa224fd`
- Source review: `.superpowers/sdd/final-review.md`
- Design: `docs/superpowers/specs/2026-07-22-quant-dashboard-design.md`
- Plan: `docs/superpowers/plans/2026-07-22-quant-dashboard.md`

## TDD evidence

Production code was unchanged while focused regressions were added. The first runs reproduced the review findings:

- `.../venv/bin/python -m unittest tests.test_web_market_data.MarketDataRepositoryTest.test_upsert_history_rejects_impossible_or_empty_bars_atomically -v`
  - RED: six subtests failed because empty, non-positive-price, negative-volume, and inconsistent OHLC frames were accepted.
- `.../venv/bin/python -m unittest tests.test_web_factors.FactorRegistryTest.test_result_json_shape_is_safe_and_stable tests.test_web_factors.BuiltinFactorTest.test_chart_rows_include_ohlcv_indicators_and_prior_changes tests.test_web_factors.BuiltinFactorTest.test_every_builtin_exposes_methodology_and_overview_metadata -v`
  - RED: factor methodology/overview fields and prior-session chart deltas were absent.
- Focused `tests.test_web_api` run covering exact-date peers, route call count, stale diagnostics, structure pivots/annotations, and stale detail state.
  - RED: the selected factor counted five peers instead of four, the bulk-read contract was absent, stale status fields were absent, and shape-specific structure fields were absent.
- Focused `tests.test_web_assets` run covering daily-return formatting, quote clearing, stale filtering/status, chart adapter series/markers/deltas, factor overview metadata, reload recovery, and expandable detail.
  - RED: all eight targeted tests failed for the expected missing behavior.

After implementation:

- `.../venv/bin/python -m unittest tests.test_web_market_data tests.test_web_update_jobs tests.test_web_factors tests.test_web_api -v`
  - GREEN: 53 tests passed.
- `.../venv/bin/python -m unittest tests.test_web_assets -v`
  - GREEN: 21 tests passed.

## Findings fixed

1. Exact-date percentile eligibility
   - The stock endpoint now derives selected history, summaries, and the peer cohort from one consistent SQL read snapshot and retains each history's actual final bar as its `AnalysisContext.observation_date`.
   - A peer missing the selected date no longer joins that selected-date cohort; four exact-date peers remain below the five-peer threshold.
   - The detail route no longer performs one SQLite read per peer, does not compute structured/missing/ineligible factors for peers, and evaluates the complete registry only for the selected ticker.

2. Daily change units
   - `summary.daily_return_unit` explicitly declares `fraction`.
   - The header formatter multiplies fractional returns by 100 and has runtime coverage for positive, negative, and zero values.

3. Stale versus inactive
   - Universe rows expose distinct `stale`, `inactive`, and `data_status` fields.
   - Stale and inactive histories retain independent shape diagnostics computed at their real observation dates.
   - The advertised combined filter includes both statuses, and row/header rendering keeps data status separate from shape state.

4. Required chart levels, annotations, volume diagnostics, and deltas
   - The structure contract exposes strict-VCP and tight-platform pivots plus shape annotations.
   - The linked volume panel plots raw volume, volume MA20, and volume ratio on a separate scale.
   - Locked detail includes volume-ratio change, pivot-distance change, and MA crossing direction.
   - Deterministic chart-adapter coverage verifies series data, pivot lines, markers, and detail values.

5. Impossible OHLCV rejection
   - A typed `InvalidMarketData` error rejects empty frames, non-positive OHLC, negative volume, and invalid high/low ordering before a write connection opens.
   - Regression coverage verifies that existing query results remain exactly equal after every rejected frame and that the update job reports a partial failure with zero updates.

6. Reload recovery
   - The update controller fetches `/api/update/status` during dashboard initialization, renders a recovered running state, disables the action, and resumes polling.

7. Factor/group metadata and expandable detail
   - Factor definitions/results now include methodology and overview participation.
   - Registry group metadata includes stable key, label, methodology, and overview opt-in.
   - Unknown groups always remain in the detail table; only explicitly opted-in groups/factors enter the overview.
   - The complete factor table is expandable.

8. Failed ticker selection
   - Close, daily change, and observation date are cleared before awaiting the new request and remain empty on failure.

## Final verification

Command:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-final-fix-pycache \
  /Users/renyinghao.1/Project/stock_screener/venv/bin/python -W error \
  -m unittest discover -s tests -v
```

Output: `Ran 116 tests in 1.983s` / `OK`.

Commands:

```bash
for file in web/static/js/*.js; do node --check "$file" || exit 1; done
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-final-fix-pycache \
  /Users/renyinghao.1/Project/stock_screener/venv/bin/python -m py_compile \
  web/app.py web/contracts.py web/services/*.py web/factors/*.py
git diff --check
```

Output: all eight JavaScript modules passed, Python compilation exited 0, and `git diff --check` produced no output.

Local-only real-database smoke:

```bash
.../venv/bin/python -c 'from web.app import app; ... Flask test client ...'
```

Output:

```text
universe 200 181
stock 200 MSFT 2026-07-20 14 500
```

Safety scans:

```bash
rg -n "★ 买点|上涨概率|目标价|胜率|buyable_now" web docs/dashboard.md || true
rg -n "/Users/|Traceback|TIINGO_API_KEY=|FINNHUB_API_KEY=|ALPHAVANTAGE_API_KEY=" web || true
rg -n "fetch\\(|https?://|_tiingo_history|Finnhub|Alpha Vantage" \
  web/factors web/services/analysis.py web/services/scenarios.py web/static/js
```

Output: no unsupported claims, absolute paths, traceback strings, or assigned secret values; the only client `fetch` is the local API wrapper in `web/static/js/api.js`.

## Self-review

- Checked every Critical, Important, and Minor recommendation in the final review against code and a focused regression.
- Confirmed percentile groups use actual dates and keep the five-peer minimum.
- Confirmed provider data is fully validated before `_connect_writable()`.
- Confirmed a benchmark that exists globally but has no bar by the selected observation date emits `missing_benchmark` and disables benchmark-relative factors.
- Confirmed no new remote viewing dependency or provider call was introduced.
- Confirmed scenario implementation and historical/non-predictive wording were untouched.
- Confirmed dynamic frontend content continues to use `textContent`, not HTML injection.

## Independent review

- First review of `48dbcf3..1506051`: no Critical or Important findings. One Minor remained because the route still evaluated all factors for all peers and used multiple read snapshots.
- Resolution in `b8a7fbc`: a single `MarketAnalysisSnapshot`, full selected-ticker evaluation, exact-date peer filtering before compute, and finite percentile-eligible peer evaluation only. Focused RED then GREEN tests cover both repository-call and factor-call behavior.
- Follow-up review of `1506051..b8a7fbc`: prior heavy-route P3 closed; real-data eligible-factor ranks, ties, peer counts, and display scores matched the original universe evaluator. The reviewer found one empty as-of SPY edge case; the exact reviewed fix and regression were committed as `4e1e147` and pass in the 116-test final gate.

## Remaining concerns

- No known correctness gap remains from `.superpowers/sdd/final-review.md`.
- Exact-date percentile calculation still necessarily evaluates eligible numeric factors for same-date peers; a repeated real-data MSFT request measured about 4.7 seconds after the optimization, down from the reviewer's approximately 6.6–7.2 seconds before it.
- This repair wave used deterministic DOM/chart-adapter tests and a real local-database Flask smoke; it did not repeat the earlier manual desktop/mobile browser session.
