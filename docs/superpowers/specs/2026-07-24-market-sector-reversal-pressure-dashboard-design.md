# Market, Sector, Reversal, and Pressure Dashboard Design

## Goal

Add a dedicated market-and-sector command center that explains the current
market posture, compares US sectors, drills into the semiconductor and adjacent
AI-infrastructure groups, and exposes two independent point-in-time scores:

- `reversal_opportunity_score`: evidence that a declining or damaged structure
  is stabilizing and turning upward;
- `downside_risk_score`: evidence that an advancing structure is meeting supply
  or beginning to break down.

The first release uses causal daily OHLCV evidence and labels all buy/sell
pressure estimates as proxies. Intraday trades, quotes, and order-book-derived
evidence are a later optional enhancement. A missing intraday feed must never
prevent the daily dashboard from working or cause a daily proxy to be described
as true order flow.

This design extends, rather than replaces, the existing causal reversal
features for prior-high breakout, descending-trendline breakout, and confirmed
higher low.

## Scope and delivery stages

The work is intentionally layered:

1. Build the daily market, sector, reversal, and pressure evidence engine.
2. Expose the evidence through a read-only API and a separate `/market` page.
3. Add atomic evidence features to the existing 5/20/60-session walk-forward
   research path.
4. Leave a provider-neutral intraday enhancement boundary for later Alpaca or
   Futu data. Do not implement order-book factors in this release.

The first detailed industry group is semiconductors. The page still compares
all major US sector ETF proxies, but constituent-level breadth and ranking are
only claimed for versioned groups whose membership is present locally.

## User experience

### Navigation and page structure

Add a top-level `Market & Sectors` / `市场与板块` navigation item next to the
existing stock dashboard. It opens `/market`.

The selected command-center layout has five layers:

1. Header controls: observation date, 1/5/20/60-session display horizon,
   language, and data freshness.
2. Market posture cards: posture, trend, breadth, distribution pressure, and
   volatility state.
3. Sector heat map: all supported sector proxies, colored by relative
   performance and annotated with downside risk.
4. Evidence chain: the values, thresholds, states, coverage, and methodology
   behind the current market conclusion.
5. Selected-sector drill-down: constituent rankings, opportunity/risk scores,
   daily pressure proxy, state, and newly changed evidence.

Clicking a sector changes only the drill-down region and does not resize or
shift the page. Clicking a stock opens the existing stock dashboard for that
ticker.

### Honest labels

Every snapshot identifies its evidence tier:

- `daily_proxy`: only daily OHLCV evidence is available;
- `intraday_enhanced`: a future implementation has added sufficiently fresh
  intraday evidence.

The first release always returns `daily_proxy`. UI copy must use “OHLCV pressure
proxy” and must not call it actual order flow, executed buying, or executed
selling.

The dashboard displays a data-coverage percentage and explicit unavailability
reasons. It never silently redistributes missing required weights.

### Score presentation

The command center shows one current deterministic evidence score for
opportunity and one for downside risk. Historical outcome estimates, when
available, are separate values for 5, 20, and 60 sessions. An evidence score is
not presented as a probability.

If the walk-forward sample gate is not satisfied, the page says that historical
probability is unavailable because evidence is insufficient. It does not
fabricate or extrapolate a probability.

## Reference universe

The fixed historical-data update pool gains:

- market: `SPY`, `QQQ`;
- US sector proxies: `XLK`, `XLC`, `XLY`, `XLP`, `XLE`, `XLF`, `XLV`, `XLI`,
  `XLB`, `XLRE`, `XLU`;
- semiconductor proxies: `SOXX`, `SMH`.

`SOXX` and `SMH` form an equal-weight semiconductor return composite. The
composite uses only aligned, point-in-time sessions. If one proxy is missing,
the available proxy may be used with degraded coverage. If both are missing,
semiconductor sector scores are unavailable.

Sector membership is stored as versioned local metadata, not inferred from
price behavior. The semiconductor drill-down separates:

- semiconductor and equipment companies, such as NVDA, AMD, AVGO, MU, INTC,
  QCOM, TXN, ADI, MCHP, MRVL, ON, NXPI, AMAT, LRCX, KLAC, TER, and ENTG;
- adjacent AI-infrastructure names, such as NBIS, ANET, DELL, HPE, and SMCI.

The second group is visibly labeled `AI infrastructure related`; NBIS and
similar names are not represented as conventional semiconductor constituents.
Only locally available tickers participate in constituent breadth and ranking.

## Architecture

### Evidence engine

Create a focused daily engine in `research/market_pressure.py`. It accepts
validated histories already truncated
to a common `asof` and returns immutable, JSON-safe evidence rows. It does not
fetch remote data, access Flask globals, or mutate input frames.

The engine contains independent units for:

- market regime and breadth;
- sector proxy returns and relative strength;
- stock-versus-sector relative strength;
- causal reversal opportunity evidence;
- downside structure and supply-pressure evidence;
- composite evidence and coverage.

Every atomic observation contains:

- stable key and version;
- numeric value when available;
- threshold or comparison value;
- `met`, `near`, `unmet`, or `unavailable` state;
- point contribution and maximum point contribution;
- lookback window;
- safe unavailability code;
- audit metadata needed to reproduce the decision.

### Data flow

```text
local daily OHLCV
  -> common as-of snapshot
  -> SPY/QQQ market state
  -> sector ETF state and relative strength
  -> selected group breadth and constituent state
  -> atomic opportunity/risk evidence
  -> deterministic evidence scores
  -> read-only market overview API
  -> market command-center page

atomic evidence only
  -> existing point-in-time forecast dataset
  -> independent 5/20/60-session walk-forward evaluation
```

Composite UI scores are not fed back into the model when their atomic
components are already model inputs. This avoids duplicating the same evidence
and hard-coding UI weights into the predictive model.

### Intraday extension boundary

A later intraday adapter may provide normalized measures such as trade
direction imbalance, order-flow imbalance, depth imbalance, cancellation
pressure, and absorption. The daily engine consumes none of those tables
directly. A separate optional enrichment interface merges only evidence with a
declared provider, coverage, event-time range, and freshness status.

No intraday value is forward-filled into a later session. Missing or stale
intraday evidence remains unavailable.

## Point-in-time factor definitions

All rolling windows exclude future sessions and operate on the observation
calendar of the relevant ticker. Thresholds prefer ATR normalization, rolling
percentiles, and relative measures over universal fixed percentage moves.

### Market posture

`market_posture_score` has four groups:

- trend, 30 points: SPY and QQQ position relative to EMA20, SMA50, SMA200, plus
  point-in-time moving-average slopes;
- breadth, 25 points: share of the local eligible universe above EMA20 and
  SMA50, plus rolling new-high/new-low participation;
- sector leadership, 25 points: 5/20/60-session sector-proxy performance and
  relative strength versus QQQ;
- distribution and volatility, 20 points: recent distribution-day evidence,
  ATR expansion, and realized-volatility change.

Interpretive bands are descriptive:

- 80–100: healthy trend;
- 60–79: risk-on with possible selectivity;
- 40–59: mixed or range-bound;
- 20–39: under pressure;
- 0–19: defensive.

### Reversal opportunity

`reversal_opportunity_score` has four groups:

- market stabilization, 20 points: QQQ stops making lower lows, recovers a
  short trend measure, or shows contracting downside range;
- sector improvement, 20 points: the SOXX/SMH composite relative-strength trend
  versus QQQ turns upward;
- stock structure, 35 points: the existing confirmed higher low, descending
  trendline breakout, and prior-high breakout evidence;
- participation confirmation, 25 points: capitulation-volume recovery,
  favorable close location, positive signed-volume proxy, and subsequent
  up-volume confirmation.

### Downside risk

`downside_risk_score` has four groups:

- market weakening, 20 points: QQQ trend breaks or accumulates distribution
  evidence;
- sector weakening, 20 points: semiconductor relative strength versus QQQ
  falls or accelerates downward;
- stock structure damage, 35 points: failed breakout, EMA20/SMA50 breakdown,
  lower-high/lower-low evidence, or stock-versus-sector relative-strength
  breakdown;
- supply pressure, 25 points: high-volume price non-progress, adverse close
  location, upper-wick expansion, or repeated high-volume down sessions.

### Daily OHLCV pressure proxy

The first release derives auditable pressure proxies from:

- close location within the daily high-low range;
- upper- and lower-wick share of true range;
- daily volume divided by its causal 20-session average;
- return per unit of relative volume, used as a price-progress efficiency
  measure;
- a signed-volume proxy equal to normalized close location multiplied by
  relative volume;
- distribution sessions: adverse return, elevated volume, and a close near the
  session low;
- failed breakouts: a known prior pivot is exceeded but the close returns below
  that pivot according to a versioned causal rule.

Zero-range sessions, non-finite OHLCV values, and insufficient rolling history
produce unavailable evidence instead of division artifacts.

## Scoring and coverage

Each atomic condition contributes zero, half, or all of its versioned weight
for `unmet`, `near`, or `met`. `unavailable` contributes neither evidence nor
available weight.

Required benchmark groups are not optional:

- market posture requires an aligned QQQ market history;
- semiconductor scores require at least one of SOXX or SMH;
- stock-sector relative strength requires an available sector composite.

If required evidence is missing or total available weight is below 80%, the
composite score is unavailable. The API still returns all available atomic
evidence. At or above 80% coverage, the score is normalized over available
weight and is always accompanied by its coverage percentage. The threshold and
normalization policy are versioned.

Opportunity and downside risk are independent. They are not complements and
need not sum to 100.

## Outcome definitions and evaluation

Evaluation is independent for 5, 20, and 60 sessions.

- Opportunity outcome: the terminal close return at the selected horizon is
  above the existing versioned positive neutral boundary.
- Downside-risk outcome: the minimum forward close return within the selected
  horizon crosses below a versioned volatility-adjusted adverse boundary.

Both outcomes may be true for the same observation if price first suffers a
material drawdown and later closes strongly. That is intentional.

Training and calibration rows must satisfy:

```text
label_end_date < evaluation_asof
```

The existing minimum sample, both-class, and out-of-sample provenance gates
apply separately to each outcome and horizon. The deterministic evidence score
remains visible when calibration is unavailable.

## API contract

Add a read-only endpoint:

```text
GET /api/market-overview?asof=YYYY-MM-DD&horizon=5&sector=semiconductor
```

The response contains:

- observation date, requested horizon, data revision, and freshness;
- market posture and atomic market evidence;
- sector rows for each available proxy;
- selected-sector composite, coverage, breadth, and atomic evidence;
- constituent opportunity/risk evidence scores and daily pressure proxy;
- newly changed evidence events;
- calibration states and results for 5/20/60 sessions;
- data tier and intraday availability;
- stable safe unavailability codes.

The endpoint reads one coherent local snapshot and performs no external network
request. Cache identity includes data revision, `asof`, horizon, selected
sector, evidence version, and model version.

Malformed parameters return stable client errors. Missing local reference
history returns typed unavailability rather than HTTP 500. Database failures
are redacted at the HTTP boundary.

## Front-end behavior

The page reuses the existing visual language, localization conventions, and
local asset policy. It does not add a new front-end framework.

- Heat-map color represents the selected-horizon relative-performance metric;
  it does not ambiguously encode the risk score.
- Every tile prints the metric and risk score so color is not the only carrier
  of meaning.
- Selecting a tile updates the drill-down without changing the overall layout
  height or the user's scroll position.
- Evidence rows expose concise Chinese labels and expandable methodology.
- Changed-event cards show only conditions newly crossed on the selected
  session, with prior and current values.
- Data coverage, evidence tier, and observation date remain visible.
- Keyboard navigation, visible focus, semantic tables, and non-color status
  labels are required.
- Responsive layouts preserve readable dates and do not cover chart axes.

## Data acquisition behavior

Reference ETFs are added to the explicit update universe. A normal page load
never starts remote data collection. If a reference ticker is absent, the page
reports it and the existing explicit update workflow may retrieve it.

The implementation must preserve the user's existing local `prices` table and
current update failure semantics. Reference-ticker additions do not silently
remove or deactivate existing tickers.

## Validation

### Causality and math

- Appending future rows cannot change any earlier evidence or score.
- Market, sector, and stock histories are truncated to one common `asof`.
- Benchmark alignment never uses a later observation to fill an earlier
  missing session.
- Unit counterexamples cover distribution, upper-wick pressure, high-volume
  non-progress, failed breakout, capitulation recovery, and relative-strength
  turns.
- Zero-range and non-finite rows fail closed.

### Coverage and degradation

- SOXX-only and SMH-only operation produces degraded but auditable coverage.
- Missing both proxies makes semiconductor scores unavailable.
- Missing QQQ makes market posture unavailable.
- Coverage below 80% never yields a composite score.
- An unavailable intraday collector leaves the daily page available and marked
  `daily_proxy`.

### Evaluation integrity

- 5/20/60-session opportunity and risk labels mature independently.
- Every training row obeys the strict label-end boundary.
- Calibration remains unavailable below existing sample and class-diversity
  gates.
- Composite UI scores are not duplicated as model features alongside their
  atomic components.

### API and UI

- API payloads are deterministic, JSON-safe, localized through stable keys,
  and contain evidence provenance.
- Sector selection updates only the detail region.
- Stock navigation reaches the existing ticker page.
- Heat-map meaning is available without color.
- Dates, tooltips, and axes remain visible at desktop and responsive widths.
- Existing stock chart hover, prediction, and update behavior does not regress.

### Completion gates

- Focused engine, API, localization, and browser-asset tests pass.
- Full repository tests pass.
- Modified Python compiles and modified JavaScript passes syntax validation.
- Diff checks are clean.
- Browser validation covers the command center, sector selection, unavailable
  data, bilingual rendering, and responsive layout.

## Implementation order

1. Add versioned benchmark and group metadata plus explicit reference-ticker
   update behavior.
2. Implement and test the pure daily evidence engine.
3. Add immutable service contracts, coherent repository reads, and the market
   overview API.
4. Build the localized `/market` command-center page.
5. Add atomic features and independent 5/20/60-session outcome evaluation.
6. Validate performance, causality, browser behavior, and compatibility.

Intraday order-flow modeling and a Futu OpenD provider remain separate future
specifications.
