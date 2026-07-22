# Quant Research Dashboard Design

## Goal

Replace the legacy score table with a local-first research dashboard that supports full-universe browsing, linked price and volume charts, extensible factor diagnostics, and explicitly non-predictive forward scenarios.

The dashboard must preserve the project's validated conclusion: the legacy stock score, VCP event detector, and momentum/VCP regression do not have reliable selection or timing alpha. Rules and factors may describe current state, compress the candidate universe, and support portfolio risk decisions; they must not be presented as buy signals, success probabilities, or validated forecasts.

## User Experience

The product is a single-page stock-pool workstation built on the existing Flask application.

### Global header

The header shows:

- application name and the research-only disclaimer;
- latest common market date and per-ticker freshness coverage;
- an `Update market data` action;
- background update state: idle, running, completed, partially completed, rate limited, or failed;
- completed ticker count, current ticker, and a resumable rate-limit message.

The update action reads prices only. It does not spend Finnhub or Alpha Vantage fundamental-data quota.

### Left stock-pool panel

The default universe is every ticker in `data/prices.db`, including inactive tickers with an explicit inactive or stale status. The panel supports:

- ticker search;
- sorting by ticker, data freshness, factor percentile, shape state, and volatility;
- filters for strict VCP, tight platform, near pivot, fresh data, and inactive/stale data;
- a compact row showing ticker, descriptive shape state, selected momentum percentile, and latest date;
- selection of one ticker to update the research panel without a full page reload.

Filtering and sorting are descriptive tools. No default sort is labelled as a recommendation ranking.

### Main research panel

The selected ticker header shows ticker, company label when available, close, daily change, observation date, and data-quality warnings. A legacy rules score may appear as `Traditional rules score`, accompanied by `Not validated for prediction`.

The primary chart area contains two vertically linked panels:

1. an OHLC candlestick chart with EMA20, SMA50, SMA200, strict VCP pivot, tight-platform pivot, and shape annotations;
2. a volume chart with raw volume, 20-day average volume, and volume ratio.

Both panels share zoom, date range, and a synchronized crosshair. Locking the pointer to a date displays:

- open, high, low, close;
- daily return and true range;
- volume, change from the prior session, 20-day average volume, and volume ratio;
- EMA20, SMA50, SMA200, and ATR20;
- distance from the applicable pivot;
- changes from the prior session, such as a newly crossed moving average, volume-ratio change, or pivot-distance change.

Supported initial date ranges are three months, six months, one year, two years, and all locally available history.

### Factor and structure panels

The dashboard displays:

- a factor overview visualization grouped into trend, momentum, VCP/structure, volume/price, and risk;
- strict VCP state, tight-platform state, pivot, distance to pivot, ATR, and key moving averages;
- an expandable factor table with raw value, same-date cross-sectional percentile, display score, observation date, missing-data reason, and factor description;
- explicit wording that factor display scores are normalized diagnostics, not probabilities.

The visualization must not hide raw factor values behind normalized scores.

### Scenario panel

The dashboard displays historical conditional scenario bands for 20, 40, and 60 trading days. The default band uses the 25th, 50th, and 75th percentiles of point-in-time historical horizon returns and converts them into paths starting from the selected observation close.

Scenario construction must:

- use only information available at or before the observation date;
- exclude overlapping samples or clearly report the sampling rule used;
- constrain implausibly wide bands using a documented current-volatility/ATR rule;
- remain independent of the legacy score and failed VCP/momentum regression direction;
- label outputs `Pessimistic`, `Median`, and `Optimistic historical scenarios` rather than target prices or forecasts;
- hide an unavailable horizon and return a specific missing-data reason;
- state that the scenarios are historical ranges, not predicted probabilities.

## Architecture

### Flask application

`web/app.py` becomes a thin application factory and route layer. It does not fetch remote data or calculate factors inside request handlers.

The application provides:

- `GET /` for the workstation shell;
- `GET /api/universe` for universe summaries and freshness metadata;
- `GET /api/stocks/<ticker>` for charts, registered factors, structures, key levels, and scenarios;
- `POST /api/update` to start one background price-only update;
- `GET /api/update/status` to poll update progress.

API errors use a stable JSON envelope with an error code and safe message. Responses never expose secrets, absolute server paths, or tracebacks.

### Market-data service

`web/services/market_data.py` owns read-only SQLite access and returns typed price histories, ticker summaries, freshness distributions, and benchmark data. It distinguishes:

- an unknown ticker;
- an empty history;
- an inactive ticker;
- a stale but otherwise valid history;
- a missing SPY benchmark;
- an indicator warm-up shortfall.

It does not silently convert missing values to zero.

### Analysis context

`web/services/analysis.py` builds one point-in-time `AnalysisContext` containing ticker, observation date, stock history truncated at that date, benchmark history truncated at that date, cached common calculations, and optional metadata. Existing factor functions are adapted behind this context instead of duplicated.

### Factor plug-in protocol

`web/factors/base.py` defines a `FactorDefinition` protocol with:

- stable `key`;
- user-facing `label`;
- `group`;
- `direction` metadata describing whether higher, lower, neutral, or non-monotonic values are preferable for display purposes;
- `compute(context)`;
- `format(value)`;
- user-facing description and methodology text.

`web/factors/registry.py` provides a `FactorRegistry`. Each registered factor returns a `FactorResult` containing:

- raw value;
- same-date cross-sectional percentile when available;
- 0-to-100 display score when meaningful;
- observation date;
- missing flag and missing reason;
- source/method version.

The first release wraps existing trend, momentum, strict VCP, tight-platform, volume/price, volatility/risk, and traditional-rule diagnostics. Adding a factor requires a new definition and tests, not modifications to the universe table or factor-detail markup. Unknown future groups render in the expandable factor table and may opt into the overview visualization through registry metadata.

### Scenario service

`web/services/scenarios.py` calculates 20-, 40-, and 60-session historical scenario distributions. It uses point-in-time price slices, returns both path points and methodology metadata, and reports sample count and missing reasons. The API schema is stable enough to permit a future validated model provider, but the initial provider is named `historical_distribution`, not `prediction`.

### Update-job service

`web/services/update_jobs.py` owns a process-local update state machine and prevents concurrent updates. It performs price-only Tiingo updates, commits each successful ticker independently, and preserves progress on interruption or HTTP 429.

States are:

- `idle`;
- `running`;
- `completed`;
- `partial`;
- `rate_limited`;
- `failed`.

Status includes start/end timestamps, total active tickers, completed count, updated count, current ticker, safe error message, and resumability. A second start request while running receives a conflict response and does not start another worker.

The service interface isolates the dashboard from the current implementation weakness where `build_local_db.py --update` invokes full fundamental fetching. The dashboard path explicitly disables fundamental fetches.

### Front end

The existing template is replaced with a single workstation shell and focused static modules:

- API client and state management;
- universe search/filter/sort;
- linked candlestick and volume charts;
- crosshair detail computation;
- factor and structure rendering;
- scenario rendering;
- update progress and errors.

The implementation uses the existing Flask stack plus a browser charting library that supports candlesticks, synchronized crosshairs, volume histograms, overlays, and responsive resizing. The selected library is vendored or served locally so the dashboard does not require a public CDN at runtime. No Node build chain or front-end framework is introduced.

## Data Flow

On initial load, the browser fetches `/api/universe`, renders the full stock pool, and selects the first valid ticker or a ticker restored from local browser state. Selecting a ticker fetches `/api/stocks/<ticker>`, then updates charts, crosshair details, structures, factors, and scenarios from one internally consistent observation date.

Starting an update returns immediately with the current job snapshot. The browser polls `/api/update/status` until a terminal state. On completion or partial completion, it refreshes universe freshness and reloads the selected ticker if its date changed.

All single-stock analysis uses the ticker's stated observation date. Cross-sectional percentiles report their peer count and common comparison date; when a same-date universe is too small, the percentile is missing rather than computed from mixed dates.

## Error and Safety Boundaries

- The dashboard is local-only and binds to loopback by default.
- Secrets remain in `env.sh` and server-side environment variables.
- Invalid ticker input is normalized and rejected before database access.
- SQLite connections are short-lived and parameterized.
- Chart endpoints cap requested history to locally available data.
- One failed factor returns a missing `FactorResult`; it does not fail the full stock response.
- A missing benchmark disables only benchmark-relative factors.
- Retired tickers remain browsable with a stale/inactive label.
- Rate limiting is a recoverable terminal job state, not a successful full update.
- Every score and scenario area carries concise research-only language.

## Testing and Acceptance Criteria

### Unit tests

Tests cover:

- factor registration, duplicate keys, group metadata, formatting, and per-factor failure isolation;
- point-in-time truncation and the absence of future bars;
- same-date percentile calculation and insufficient-peer handling;
- scenario quantiles, sample counts, non-overlap policy, volatility constraint, and missing horizons;
- crosshair data values and prior-session changes;
- update-state transitions, mutual exclusion, partial commits, and rate-limit recovery;
- inactive, stale, unknown, and insufficient-history tickers.

### API tests

Flask test-client coverage verifies:

- the universe schema and freshness counts;
- a valid stock payload;
- unknown and malformed tickers;
- missing benchmark behavior;
- starting an update, rejecting a concurrent update, polling status, and rate-limited results;
- safe errors without secrets, absolute paths, or tracebacks.

### Front-end checks

Automated or deterministic browser checks cover:

- universe search, sort, and filters;
- ticker selection without full-page reload;
- three-month through all-history ranges;
- synchronized candlestick/volume crosshairs;
- locked OHLCV and indicator details;
- scenario-horizon visibility and missing messages;
- update progress and resumable 429 messaging;
- desktop and narrow-screen layouts.

### Completion gate

The feature is complete only when:

- the full Python test suite passes;
- JavaScript/static checks pass;
- the dashboard loads using only the local database with network disabled;
- an update task can be tested with a fake provider without consuming API quota;
- browser verification confirms the linked charts and locked crosshair values;
- the page contains no buy-point badge, validated forecast probability, or unsupported alpha claim.

## Initial Scope Exclusions

- live intraday data;
- brokerage execution;
- portfolio order entry;
- user accounts or remote hosting;
- options data;
- a trained price-target model;
- automatic fundamental-data refresh;
- React, Vue, or a Node build pipeline.

These exclusions keep the first release a testable local research workstation while preserving clear service and factor-provider interfaces for later expansion.
