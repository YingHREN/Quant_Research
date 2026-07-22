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
./venv/bin/python -m py_compile web/app.py web/contracts.py web/services/*.py web/factors/*.py
git diff --check
```

Then start the dashboard on loopback, exercise both local read APIs, and perform
a desktop and narrow-width browser pass. Confirm filtering and ticker changes,
all five chart ranges, linked hover/click-to-lock detail, the volume panel,
missing factor and scenario states, stale/inactive status, update progress with
a fake provider, and a browser console free of uncaught errors.
