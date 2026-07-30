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

## Prospective downside shadow evaluation

The shadow experiment is an offline research workflow. It does not run inside
Flask requests and has no authority to change Ridge, TOPRISK, or the final
forecast direction. The frozen model artifact is tracked in Git; the append-only
SQLite ledger is local data and must not be committed.

Freeze a new immutable experiment only once:

```bash
./venv/bin/python -m research.run_downside_shadow freeze \
  --research-database data/research_prices.db \
  --shadow-database data/downside_shadow.db \
  --model-artifact reports/downside-shadow-v1-model.json \
  --experiment-id downside-shadow-v1
```

After a genuinely new completed US market session has been added to the
research database, capture only that latest session:

```bash
./venv/bin/python -m research.run_downside_shadow capture \
  --research-database data/research_prices.db \
  --shadow-database data/downside_shadow.db \
  --model-artifact reports/downside-shadow-v1-model.json \
  --experiment-id downside-shadow-v1
```

`capture` never replays missed dates and rejects the freeze date or any earlier
date. A same-day retry is idempotent; changed payloads fail closed. Finally,
settle only future paths whose 5/10/20-session windows are complete:

```bash
./venv/bin/python -m research.run_downside_shadow evaluate \
  --research-database data/research_prices.db \
  --shadow-database data/downside_shadow.db \
  --model-artifact reports/downside-shadow-v1-model.json \
  --experiment-id downside-shadow-v1
```

The generated report explicitly identifies prospective results, capture gaps,
unavailable/not-applicable outputs, and `online_authority=none`. Reaching its
research gate permits human review only; it never grants an automatic downside
veto.

## Support first-touch reaction research

The support first-touch study is also offline and advisory-only. It freezes each
support zone at the observation close, waits 5/10/20 sessions for its first
touch, and classifies the touch day plus two following sessions as accepted,
failed, or ambiguous. Untouched episodes remain coverage evidence but are not
included in reaction-rate denominators.

Run the fixed development and confirmation cohorts with:

```bash
./venv/bin/python -m research.run_support_touch_reaction_study \
  --database data/research_prices.db \
  --asof 2026-07-24 \
  --start 2018-01-01 \
  --cohort-size 240 \
  --folds 5
```

The tracked Markdown, CSV, and JSON artifacts are
`reports/support-touch-reaction-study.*`. Strict baseline/challenger distance
slices use the baseline event's distance bin for both rows so every paired
slice compares the same events. The 2026-07-30 run did not promote the model:
the confirmation acceptance-rate gain was only 0.50 percentage points, the
confirmation cohort covered only one of the three preregistered groups, the
historical group audit failed, and no future temporal holdout exists. This
workflow does not change API payloads, charts, Ridge, downside vetoes, or the
final policy.

## Free intraday collector

The collector is a separate foreground process so opening or hovering the
dashboard cannot change subscriptions:

```bash
source env.sh
export ALPACA_API_KEY="..."
export ALPACA_API_SECRET="..."
./venv/bin/python collect_intraday.py \
  --selected AMD --peer NVDA --peer AVGO --candidate NBIS
```

Press `Ctrl-C` in that terminal to stop the foreground collector; it finishes
its collector cleanup before returning to the shell.

This phase uses Alpaca's free IEX feed, not the full US consolidated market.
Trade direction is inferred from the contemporaneous quote midpoint and then
the tick rule; it is not exchange-provided aggressor direction. Inspect
`GET /api/market-data/status` for coverage, active symbols, freshness, and
disconnects.

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

### Ten-year daily-history foundation

The normal dashboard update intentionally requests only a one-year overlap so
provider corrections can replace recent rows without downloading the full
research history every day. It never deletes older rows that are absent from a
response.

Use the explicit DATA-001 backfill to request ten years for every locally known
symbol plus the fixed market and sector references:

```bash
source env.sh
unset FINNHUB_API_KEY ALPHAVANTAGE_API_KEY
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python build_local_db.py --backfill-years 10 --workers 4

./venv/bin/python build_local_db.py --coverage
```

Long and short provider requests use different cache keys, so a cached
one-year response cannot satisfy a ten-year request. Each successful write adds
an append-only `price_ingestions` record and refreshes `price_coverage` with
the provider, request start, UTC fetch time, source start/cutoff, source row
count, deterministic response revision, persisted coverage, and basic quality
counts. Existing `prices` readers remain unchanged.
The explicit backfill downloads up to four symbols concurrently but validates
and commits them on one thread. The ordinary dashboard update stays
single-worker. Unsetting the optional fundamental-data keys prevents this
price-only operation from spending quota on unrelated financial statements.
If Tiingo returns HTTP 429, rerun the same command after the limit resets:
symbols with a successful ingestion for the same provider and requested start
are skipped, so the command resumes from the pending suffix.

The current Tiingo bars use split-and-dividend-adjusted OHLCV
(`split_dividend_adjusted`). This is appropriate for return and technical
factor calculations, but it is not a complete point-in-time corporate-action
store. Raw prices, split/dividend events, symbol changes, delistings,
historical membership, exchange-calendar gap reconciliation, and
feature-specific `available_at` timestamps remain separate DATA-001 work.
Likewise, an ingestion timestamp records when this local copy was fetched; it
must not be interpreted as the original publication timestamp of older bars.

## Market and sector command center

Open `http://127.0.0.1:5000/market`, or use the `市场与板块` / `Market &
sectors` navigation link. The page reads
`GET /api/market-overview?horizon=5&sector=semiconductor`; supported horizons
are exactly 5, 20, and 60 observed sessions. Selecting a heat-map tile changes
the sector query and drill-down while the stock links return to the existing
stock dashboard. Page and API reads are local and never start a provider
request or the intraday collector.

The fixed reference pool is:

```text
SPY QQQ
XLK XLC XLY XLP XLE XLF XLV XLI XLB XLRE XLU
SOXX SMH
```

The eleven Select Sector SPDR symbols are ETF performance proxies. They are not
treated as constituent breadth. Semiconductor context separately uses an
equal-weight composite of aligned SOXX and SMH daily returns. If one is
missing, its benchmark coverage is 50%; if both are missing, semiconductor
opportunity and risk scores are unavailable. The versioned semiconductor stock
list and the separately labelled AI-infrastructure-related list drive stock
breadth and drill-down. For example, AMD is a semiconductor constituent while
NBIS is related AI infrastructure; the UI does not relabel NBIS as a chip
company. Other sector tiles remain ETF-proxy-only until a versioned constituent
list is explicitly added.

Every explicit price update unions the fixed reference pool with locally active
tickers, removes duplicates, and preserves deterministic order. Thus a missing
QQQ, sector ETF, SOXX, or SMH can be recovered with the normal `Update market
data` action, subject to the provider's availability and rate limit. Reference
ETFs are never fetched merely by opening `/market`.

### Independent evidence scores

The command center exposes three descriptive scores:

- market posture;
- reversal opportunity;
- downside risk.

Opportunity and downside risk are independent questions and are not forced to
sum to 100. Each evidence row retains its raw value, met threshold, near
threshold, direction, lookback window, state, points, and missing reason. A
`near` state receives half weight. A composite is unavailable if a required
benchmark group is missing or less than 80% of its evidence weight is
available. These deterministic evidence scores are not probabilities.

Only atomic evidence enters the ridge feature matrix:

```text
pressure_close_location
pressure_upper_wick_ratio
pressure_signed_volume_proxy
pressure_distribution_day
pressure_failed_breakout
qqq_trend_state
sector_relative_strength_20
stock_sector_relative_strength_20
```

The three composite scores are deliberately excluded to avoid counting the
same inputs twice. Stock/sector relative strength is populated only for the
versioned semiconductor and related AI-infrastructure names in this release;
no sector is inferred from similar-looking price behavior.

### Daily OHLCV pressure proxies

For a daily bar with range `R = High - Low` and a prior-20-session average
volume that excludes the current session:

```text
close_location =
    ((Close - Low) - (High - Close)) / R

upper_wick_ratio =
    (High - max(Open, Close)) / R

volume_ratio =
    Volume / mean(previous 20 session volumes)

signed_volume_proxy =
    close_location * volume_ratio
```

Zero-range or zero-baseline rows remain unavailable rather than infinite.
`distribution_day` requires a negative close-to-close return, volume ratio at
least 1.2, and close location at or below -0.4.
`high_volume_non_progress` requires volume ratio at least 1.5 with absolute
return no greater than 0.5%. `failed_breakout` means the session high clears
the prior 20-session highest close while the close finishes back at or below
that level. `capitulation_recovery` requires a high-volume weak close followed
by an up close with close location at least 0.4.

These are daily price/volume proxies. They are not bid/ask aggressor volume,
level-2 depth, exchange order flow, or evidence of an identified buyer or
seller. The API reports `evidence_tier: daily_proxy` and
`intraday.state: unavailable`. A later broker integration may add an explicit
`intraday_enhanced` tier without changing or silently reinterpreting the daily
fields.

### 5/20/60 outcome calibration

Historical calibration keeps the opportunity and risk outcomes independent:

```text
opportunity_h =
    terminal return after h sessions > {1%, 2%, 4%} for h={5,20,60}

risk_barrier_h =
    max({1%, 2%, 4%}, ATR20 / Close)

downside_risk_h =
    minimum close return during the next h sessions < -risk_barrier_h
```

Both outcomes can be true when price first falls through the risk barrier and
then finishes above the opportunity band. Incomplete future windows remain
missing, not false. A historical row is eligible only when its explicit
`label_end_date < evaluation_asof`; equality is excluded. Monotonic empirical
calibration requires at least 100 eligible score/outcome pairs and both
classes. Otherwise probability stays `null` with
`insufficient_calibration_samples`, `calibration_requires_both_classes`, or
`score_unavailable`. The deterministic score remains visible even when
calibration is unavailable.

Safe unavailable codes identify missing market or sector benchmarks,
insufficient coverage/history, and missing local market data without exposing
database paths or provider details. Use the explicit update control to recover
missing reference histories; do not weaken the coverage gate or substitute a
future observation.

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

The chart also exposes a causal near-resistance zone for every observation
date. Candidate levels include EMA20, SMA50, SMA200, the preceding 10-session
high, confirmed swing highs, the then-known descending trendline, and the
preceding 20-session pivot. Adjacent candidates within `0.5 * ATR20` are
clustered; the nearest cluster strictly above the selected close becomes the
zone. The selected-date details show its lower and upper edges, center,
distance from the close, evidence sources, a 0–100 descriptive strength score,
and an optional farther structural resistance.

The orange zone layer uses the latest observation while the chart is unlocked
and the locked observation while a historical date is pinned. Free hover never
changes the layer. It uses only existing chart dates and opts out of price-axis
autoscaling, so inspecting a different date cannot extend, shift, or rescale
the timeline. These levels describe where supply may appear; they are not
price targets or trading instructions.

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

While a missing historical forecast is being computed, the selected-date
panel shows an explicit calculating state. A failed request becomes a visible
date-specific error while OHLCV and causal trend evidence remain available;
the previous date's forecast is never left on the chart.

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

### Unified risk decision

The Ridge return and `raw_direction` remain immutable model outputs. Every
available forecast also carries a nested, versioned `decision` record. The
decision layer combines the same-date eight-condition bearish overlay with
three point-in-time 5–10-session remembered downside-risk sources:

- individual 12-rule market/sector/stock risk;
- group breadth and volume stress;
- persistent slow decline.

```text
immediate >= 70                         -> override to down
source-specific high + immediate >= 40 -> override to down
source-specific high                   -> downgrade raw up to neutral
source-specific watch                  -> watch; retain raw direction
otherwise                              -> retain raw direction
```

Version 2 source thresholds are individual 20/30, group 40/60, and slow
decline 50/70 for watch/high. They differ because the source scores have
different empirical distributions.

The displayed `direction` is the final decision, while `raw_direction` and
`predicted_return` continue to show exactly what Ridge produced. The decision
also reports its action, stable reason codes, policy identity, raw and
remembered risk scores, active sources, memory state, and memory age. Scores
are rule scores, not calibrated probabilities. Tickers without an explicit
market-group mapping report persistent risk as unavailable; the service does
not infer or fabricate a sector.

The software group uses IGV and XSW as primary references. When neither is
locally available, XLK is an explicitly declared fallback for constituent
feature continuity; primary benchmark coverage remains 0% and the API reports
`source_tickers: ["XLK"]`. The fallback is therefore visible rather than
being misreported as complete software-sector evidence.

`ForecastService` builds the risk context once per database revision and
looks up only the exact `(ticker, observation_date)` row. Appending future
history cannot change an earlier decision. The browser presents the raw model
and policy conclusion separately so a positive Ridge return that is
downgraded or vetoed remains visible and auditable.

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

## Historical demand support

The selected-date detail and unified model-output panel expose a remembered
historical-demand zone derived from causal daily demand events, Pocket Pivots,
ATR-normalized clustering, accepted retests, decay, and explicit invalidation.
It is labelled `research` / `advisory`. It is neither an institutional cost
line nor a probability, and it cannot change Ridge or the final forecast
policy.

The frozen 2018-01-01 through 2026-07-24 study evaluated 235 sufficiently
mature stocks and 4,090,301 mature 5/10/20-session observations. On the primary
10-session strictly paired sample, baseline support held 42.28% of the time;
replacing or adding the historical-demand zone held 40.82%. All five folds and
all three stock groups were negative, so the model did not pass promotion.
The historical group intervals also contain present-day backfill assumptions,
so the point-in-time assignment audit fails closed.
The UI remains useful for explaining a possible remembered demand location,
but users must not read the displayed score as validated predictive advantage.

Run the deterministic study with:

```bash
./venv/bin/python -m research.run_historical_demand_support_study \
  --database data/research_prices.db \
  --asof 2026-07-24 \
  --start 2018-01-01 \
  --max-tickers 240
```

The concise decision report, full stratified metrics, and run manifest are
`reports/historical-demand-support-study.md`,
`reports/historical-demand-support-study.csv`, and
`reports/historical-demand-support-study.json`.

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

## Unified downside benchmark cache

The offline unified downside benchmark keeps an independent, content-addressed
cache at `data/unified_benchmark_cache.db`. It stores only statistical and rule
prediction frames. Labels, point-in-time groups, metrics, promotion gates, and
reports are recomputed on every run. The cache has no online model authority
and is not read by the dashboard.

Run the benchmark twice with the same command. The first complete run builds
and commits artifacts only after the report publishes; the second run can
restore both prediction stages:

```bash
./venv/bin/python -m research.run_unified_downside_benchmark \
  --database data/research_prices.db \
  --cache-database data/unified_benchmark_cache.db \
  --output-directory reports
```

Use `--no-cache` for an explicitly cold run. Use `--rebuild-cache` to skip
reads and repair an exact artifact identity only when the stored row is proven
corrupt. These flags are mutually exclusive. Relevant uncommitted code changes
disable both cache reads and writes, so experimental source cannot silently
reuse an artifact produced by different code.

Management commands emit stable JSON and do not print payloads, database paths,
environment variables, or provider credentials:

```bash
./venv/bin/python manage_unified_benchmark_cache.py \
  status --database data/unified_benchmark_cache.db
./venv/bin/python manage_unified_benchmark_cache.py \
  verify --database data/unified_benchmark_cache.db
./venv/bin/python manage_unified_benchmark_cache.py \
  prune --database data/unified_benchmark_cache.db --keep-per-stage 3
./venv/bin/python manage_unified_benchmark_cache.py \
  prune --database data/unified_benchmark_cache.db \
  --keep-per-stage 3 --apply
```

`status` and `verify` are read-only. `prune` is preview-only unless `--apply`
is present, and deletion targets exact artifact keys selected by the preview
ordering. The cache database and its SQLite WAL/SHM files are local derived
data and must never be committed to Git.

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
