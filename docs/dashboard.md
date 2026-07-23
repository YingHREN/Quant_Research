# Quant dashboard operating guide

The dashboard is a local research interface over `data/prices.db`. It binds to
the loopback interface and is intended to be viewed only from the same machine.
It is not an authenticated or internet-facing service.

## Start the dashboard

From the main checkout:

```bash
cd /Users/renyinghao.1/Project/stock_screener
source env.sh
./venv/bin/python web/app.py
# Open http://127.0.0.1:5000
```

The stock pool, charts, factors, and historical scenarios load from the local
SQLite database. Viewing them does not require a market-data credential and
does not make a provider request. Stop the server with `Ctrl-C` in the terminal
where it is running.

The interface defaults to Simplified Chinese (`zh-CN`). The `中文` / `EN`
control changes the language without reloading and stores the validated choice
under the local-storage key `quant-dashboard-locale`. Chart ticks are always
`MM-DD`, while details and observation dates are always ISO `YYYY-MM-DD`; both
formats are deliberately independent of browser and operating-system locale.

## Update market data

`Update market data` is an explicit, price-only refresh. It fetches Tiingo EOD
OHLCV history for each active ticker and writes validated histories to
`data/prices.db`, one ticker transaction at a time. It does not fetch or update
company fundamentals. Only one update job can run at once.

The header reports progress as completed tickers out of the total. Individual
provider failures produce a partial result after the remaining tickers have
been attempted. If Tiingo returns HTTP 429, the job stops in a rate-limited,
resumable state and preserves the unprocessed ticker suffix. Wait for the
provider limit to reset, then use the same button, now labelled `Resume price
update`; already completed tickers are not repeated.

Back up the database before an update if you need a restorable snapshot. Do not
close the process while a ticker transaction is being committed.

## Add or change factors

The factor plug-in contract is `web/factors/base.py`. A factor supplies a unique
key, label, group, direction, description, version, `compute(context)`, and
`format(value)`. `web/factors/registry.py` evaluates registered factors, isolates
factor failures, and calculates same-observation-date peer percentiles where a
sufficient numeric cohort exists.

First-party implementations and the ordered default registration live in
`web/factors/builtin.py`. Add a factor implementation there (or import one from
a new module) and include it in `build_default_registry()`. The dashboard UI is
payload-driven: registered factor metadata and results populate the overview
and detail table without adding a factor-specific front-end branch. Add tests
under `tests/test_web_factors.py` and API contract coverage when the serialized
shape changes.

Factor evaluation receives `AnalysisContext` from `web/services/analysis.py`.
Use `history_asof()` and `benchmark_asof()` so calculations remain truncated at
the observation date, and use `cached()` for shared intermediate calculations.
Return `None` when a value is unavailable; the API and UI expose the missing
state rather than inventing a score.

Every translated factor keeps the top-level English compatibility fields and
adds an `i18n` mapping. A complete Simplified Chinese entry has all five fields:
`label`, `description`, `methodology`, `window`, and `direction`. For a
first-party factor, add its English window to `FACTOR_WINDOWS`, add the complete
`zh-CN` mapping to `FACTOR_ZH`, and let `build_default_registry()` attach both.
If the factor introduces a group, add the corresponding complete entry to
`GROUP_ZH` and register a `FactorGroup`; a custom factor may instead provide
`i18n={"zh-CN": {...}}` and `group_i18n` directly. Keep technical identifiers
such as ticker symbols, VCP, EMA20, SMA50, SMA200, ATR20, and OHLCV unchanged.
Extend `tests/test_web_factors.py` to require complete metadata and
`tests/test_web_assets.py` when the popover rendering or interaction changes.

## Causal reversal factors

The dashboard exposes three event factors on every historical chart date:

- `prior_high_breakout`: close crosses above the highest close in the previous
  20 sessions, excluding the observation session.
- `trendline_breakout`: close crosses above a descending resistance line formed
  by the latest two lower confirmed swing highs.
- `higher_low_confirmed`: a newly confirmed swing low is higher than the
  preceding confirmed low by at least `0.25 * ATR20`.

Swing extrema are not visible on their pivot dates. They become usable only
after a later move confirms the reversal threshold, and the API retains both
the pivot date and confirmation date. This delay is intentional: drawing a
historical trendline from pivots discovered with later prices would leak future
information.

`reversal_signal_count` is the number of these events occurring on the same
session; `reversal_candidate` becomes true at two or more. These fields are
descriptive research markers, not validated entry signals. The three numeric
component columns are also included in `web.forecasts.dataset.FEATURE_COLUMNS`
so the existing purged 5/20/60-session ridge workflow can estimate their
incremental value. They must remain separate during evaluation so a weak
component is not hidden inside the composite; the redundant composite count is
deliberately excluded from the regression feature matrix.

The price chart renders the point-in-time descending resistance series. Hover
or lock any date to inspect the then-known resistance, event states, condition
count, and the two source high dates. The price and volume panels reduce the
chart canvas by a dedicated bottom gutter so both time axes remain readable
instead of being clipped by the following panel.

Historical model projections are loaded lazily. Hovering or locking a date
without a forecast calls
`GET /api/stocks/<ticker>/forecasts/<YYYY-MM-DD>` for that trading session
only. The service reuses revision-scoped model artifacts and caches the
single-date result; the browser also deduplicates requests for dates already
visited. A solid, highlighted `Model forecast` line starts at the selected
close and ends at the model-implied close on the selected 5/20/60-session
target date. The start marker includes the selected direction so a historical
projection remains visible even when the crosshair leaves the candle. This
line is distinct from the descending structural resistance line and does not
represent a guaranteed path between its two endpoints.

The detail panel also evaluates point-in-time evidence for two independent
questions: which conditions would strengthen an advance, and which conditions
would accelerate a decline. Each condition is `met`, `near`, `not met`, or
`unavailable`, and displays the observation, threshold, and distance when the
inputs permit it. The checks include prior-high and descending-trendline
breakouts, higher lows, trend support, support loss, lower-low risk,
distribution volume, volatility expansion, failed breakouts, and same-date
momentum. They only read rows through the selected date. Momentum is explicitly
unavailable unless a factor observation for that exact date is present; a
future or latest-only factor value is never backfilled into history. These are
diagnostic conditions for comparing the model with realized prices, not
trading instructions.

## Direction forecasts

The chart direction model is separate from the descriptive 20/40/60-session
scenario panel. Forecast targets are exactly the close-to-close return after 5,
20, or 60 observed trading sessions:

```text
target_return_h[i] = close[i + h] / close[i] - 1
SUPPORTED_HORIZONS = (5, 20, 60)
NEUTRAL_BANDS = {5: 0.01, 20: 0.02, 60: 0.04}
```

The selected horizon defaults to 20 sessions. Predicted returns above the
positive band are `up`, returns below the negative band are `down`, and both
band boundaries are inclusive `neutral`: 5-session ±1%, 20-session ±2%, and
60-session ±4%. These versioned thresholds classify a model return; they are
not target prices, expected profits, or recommendations.

Each row stores `label_end_date_<horizon>`. At forecast date `t`, training may
use features observed through `t`, but includes a labeled row only when its
label end is strictly before `t`. Feature imputation, means, and scales are fit
only on that eligible training set. The live model requires 30 finite training
targets and reports a typed unavailable state for insufficient history or
samples, a degenerate target, or a model error.

`up_probability` is intentionally absent unless calibration succeeds. The gate
requires at least 100 earlier, out-of-sample predictions for the same ticker,
horizon, model key, and model version; their outcomes must already be observable
and must contain both horizon-specific classes: `up` means the realized return
is greater than +1%, +2%, or +4% for 5, 20, or 60 sessions, respectively, while
everything at or below that positive band is non-up. A positive return inside
the neutral band is therefore not an up outcome. Otherwise the response
preserves `null` with `insufficient_calibration_samples` or
`calibration_requires_both_classes`. The default local service does not load a
persisted calibration history, so a predicted return and direction can be
available while probability remains absent. Never derive a probability by
rescaling the raw return.

### Add a forecast provider

A provider exposes non-empty `model_key` and `model_version` strings and a
`forecast_series(ticker, dates, horizons)` method returning one validated
`ForecastResult` for every requested date/horizon pair, in date-major order.
Use the shared `SUPPORTED_HORIZONS`, preserve typed unavailable results, and
obey the same `label_end_date < forecast date` leakage boundary. Unit-test the
provider in `tests/test_web_forecasts.py`, including unavailable paths and a
future-data trap.

For duplicate-safe discovery, create a registry and call
`ForecastRegistry.register(provider)`; duplicate `model_key` values are
rejected. Production serving is factory-based because the provider is built
from the current point-in-time frame. Supply a callable factory whose own
`model_key` and `model_version` match every provider it creates, construct
`ForecastService(provider_factory=factory)`, and inject that service through
Flask config as `FORECAST_SERVICE`. Add API isolation, payload-shape, and cache
tests under `tests/test_web_api.py`.

The service builds a full feature/target frame and provider on first use for a
database revision and reuses those immutable artifacts across stock requests.
A later, richer point-in-time snapshot can rebuild them within the same
revision. A deterministic full-content fingerprint also forces a rebuild when
OHLCV values are corrected without changing the row count or latest date.
Completed response bundles use the exact key
`(database_revision, ticker, first_chart_date, last_chart_date, model_version)`;
a second identical request returns a defensive copy. Snapshot metadata is
checked before that exact-cache lookup. Invalidation advances the revision and
clears both layers.

Every update run records whether at least one ticker write committed during
that run. If so, it invalidates forecasts while the public job state is still
`running`, before publishing `completed`, `partial`, `rate_limited`, or `failed`.
A resumable run that later commits more writes invalidates again. A terminal run
with zero committed writes retains the cache. If invalidation itself fails, the
job is published as `failed` with `cache_invalidation_error`; it is never
reported as successful over stale forecasts.

Ticker transactions become visible before the whole update reaches a terminal
state. A stock request therefore checks the update state after loading and
analyzing its repository snapshot. While the update is still `running`, chart
and factor data may reflect already committed ticker writes, but forecasts and
evaluation are suppressed with typed `update_in_progress` values. If the update
finishes during the request, terminal invalidation has already advanced the
forecast revision, so the request rejects its older snapshot instead of
combining it with a cached forecast. If a new update starts only after this
barrier, the already captured chart/factor snapshot predates its writes; the
forecast either remains on that same preceding revision or is rejected if the
new run manages to invalidate first.

### Synchronous forecast and evaluation budget

The stock request computes only the latest requested chart date for all three
horizons. Older chart dates are deliberately sparse and render a typed
`not_precomputed` state rather than reusing the latest model or pretending full
coverage. `forecasts.date_coverage` reports `requested_date_count`,
`computed_date_count`, the exact `computed_dates`, the
`latest_only_synchronous` policy, and the omission reason.

Exhaustive walk-forward evaluation is not run inside a stock request. The
production response retains the complete per-horizon evaluation schema with
`sample_count: 0`, null metrics, and `unavailable_reason: not_precomputed` until
revision/model-specific evidence is produced offline and integrated by a
separate ingestion path. The documented offline command does not populate the
API automatically. This keeps request latency bounded without presenting
partial rows as a full walk-forward evaluation.

## Historical scenario methodology

`web/services/scenarios.py` builds descriptive 20-, 40-, and 60-session paths
from data available at the selected observation date. For each horizon it
samples historical close-to-close returns in non-overlapping blocks, requires
at least eight samples, and reports the 25th, 50th, and 75th percentile paths as
pessimistic, median, and optimistic historical scenarios. Quantile returns are
capped in magnitude at three times the current 63-session realized-volatility
scaling for that horizon.

These ranges are historical distribution summaries. They are not forecasts,
target prices, probabilities, recommendations, or evidence of alpha. Factor
display scores are descriptive rankings within an available same-date cohort,
not probabilities or validated trading signals. The project makes no claim
that any factor, scenario, or dashboard output provides predictive alpha.

## Data freshness and inactive tickers

The latest date in the database is the dashboard's reference date. A ticker is
marked inactive when its own latest bar trails that reference by more than 20
calendar days. Smaller lags are displayed as stale/freshness metadata. Inactive
and stale histories remain available for inspection; their status is not a
recommendation. Missing benchmark history, short indicator history, missing
factor values, and insufficient scenario samples are surfaced explicitly.

## Security and local-only operation

Keep `env.sh` local and out of version control. It may define
`TIINGO_API_KEY`, `FINNHUB_API_KEY`, and `ALPHAVANTAGE_API_KEY`; never paste
their values into documentation, logs, screenshots, issues, or commits. The
dashboard's viewing APIs use only the local database. Provider access occurs
only after an explicit price-update request, and that update path uses Tiingo
prices without calling the Finnhub or Alpha Vantage fundamentals paths.

Do not change the Flask host from `127.0.0.1` or expose port 5000 through a
proxy, tunnel, or firewall rule. The application has no login, authorization,
TLS termination, or production hardening.

## Verification after changes

From the repository root, run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -W error -m unittest discover -s tests -v
for file in web/static/js/*.js; do node --check "$file"; done
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m py_compile web/app.py web/contracts.py web/services/*.py web/factors/*.py web/forecasts/*.py
git diff --check
```

Then start the dashboard on loopback and load one stock twice to exercise the
cold and revision-cache paths. Confirm that `date_coverage` declares the bounded
synchronous policy and that evaluation evidence is typed `not_precomputed`
rather than partially populated. Separately perform desktop and 390x844 browser
passes. Confirm
the Chinese default, English switch and reload persistence, a `07-17` tick, an
ISO detail date, factor popover pointer and keyboard behavior, all 5/20/60
forecast controls, linked crosshair hover and click-lock, forecast unavailable
state, no horizontal overflow, and a browser console free of uncaught errors.
