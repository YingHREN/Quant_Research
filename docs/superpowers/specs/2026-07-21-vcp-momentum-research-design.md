# VCP Historical Events and Momentum Research Design

## Objective

Build a reproducible, leakage-safe research pipeline that identifies historical
VCP pattern stages and tests whether pre-event momentum or other observable
features add out-of-sample information about subsequent outcomes.

The project must not assume that VCP or momentum predicts returns. The first
deliverable is reliable event measurement. Predictive claims are permitted only
after the preregistered validation gates pass.

## Scope

This iteration covers:

- historical, as-of-date VCP structure detection;
- explicit event stages: `forming`, `near_pivot`, `breakout`, and `invalidated`;
- standard momentum and relative-momentum features;
- an immutable event-level research table;
- regression, matched-control, and ablation analysis;
- tests for pattern geometry, timestamps, execution timing, and leakage.

This iteration does not cover live order execution, portfolio optimization,
fundamental-data modeling, neural sequence models, or claims of tradable alpha.

## Research Principles

1. A detector describes information available at the close of date `t`.
2. Any executable outcome begins at the open of the next available trading day.
3. Pattern classification is separate from return labeling and portfolio logic.
4. Feature definitions and validation gates are frozen before examining final
   test results.
5. Every tested model and parameter family is recorded, including failures.
6. VCP-only, momentum-only, and interaction models are compared on identical
   observations and dates.
7. The current two-year database is development data. It is insufficient by
   itself to establish durable alpha.

## Architecture

### 1. Market Data Access

A research data loader reads OHLCV from `data/prices.db` and returns data aligned
to an explicit trading calendar. It must expose whether a ticker has a bar on a
given date. A missing ticker bar must never be silently replaced with an older
close for simulated execution.

### 2. VCP Structure Detector

The detector accepts an OHLCV slice ending on an as-of date and returns a typed
structure containing:

- base start and end dates;
- base length, depth, and trend context;
- ordered contraction legs with peak date, trough date, prices, and depth;
- adaptive ZigZag threshold;
- contraction-depth slope and last/first ratio;
- leg volume statistics and terminal volume dry-up;
- terminal price-range contraction;
- pivot price and pivot source date;
- distance to pivot;
- rejection reasons;
- pattern stage.

The first implementation remains deterministic and interpretable. Swing geometry
uses highs and lows for leg measurements. Close prices may be used to confirm
trend state, but cannot be the sole source of swing depth.

Base selection evaluates candidate windows between 20 and 80 trading days and
uses an explicit deterministic objective. It must not simply accept the first
longest window. The objective favors a bounded base, multiple alternating swings,
contraction quality, and a recent terminal contraction. Exact weights and
tie-breaking rules are fixed in the implementation plan before research results
are generated.

The latest unconfirmed swing is returned separately from confirmed ZigZag legs.
It cannot be silently treated as a confirmed contraction.

### 3. Event State Machine

For each ticker and as-of date, the scanner transitions among these states:

- `none`: no qualifying structure;
- `forming`: qualifying base and at least two decreasing contractions;
- `near_pivot`: `forming` and close is within a preregistered distance of pivot;
- `breakout`: close crosses the previously known pivot with a separately reported
  volume ratio;
- `invalidated`: a previously active pattern violates its structural stop or
  trend gate before a valid breakout;
- `expired`: no transition occurs within the maximum event lifetime.

The state machine stores both first-observed dates and subsequent transitions.
Repeated daily detections for the same base are one event, not independent events.
Events are identified using ticker plus stable base identity; event deduplication
must not use future outcomes.

Volume confirmation is an event attribute, not part of the economic outcome
label. This preserves separation between detector and label.

### 4. Momentum Features

Momentum features are computed using data known at date `t`:

- `mom_3_1`: return from approximately 63 to 21 trading days before `t`;
- `mom_6_1`: return from approximately 126 to 21 trading days before `t`;
- `mom_12_1`: return from approximately 252 to 21 trading days before `t`;
- corresponding excess returns over SPY;
- same-date cross-sectional percentile ranks for each momentum horizon;
- `ret_1m`: the most recent approximately 21-day return, kept separate from
  medium-term momentum;
- volatility-adjusted versions based on trailing realized volatility;
- optional market-beta residual momentum as a preregistered secondary family.

Missing-history flags accompany every feature. Short-history approximations do
not masquerade as 12-month momentum.

### 5. Immutable Event Table

One row represents one event at one designated observation point. The primary
observation point is the first `near_pivot` date. Separate tables or an explicit
`observation_stage` column represent `forming` and `breakout` analyses; stages
must not be pooled without a stage term.

Required columns include:

- event and ticker identifiers;
- observation date and next executable date;
- base and contraction geometry;
- pivot and volume attributes;
- momentum features and missingness flags;
- SPY market state and trailing volatility;
- future raw and SPY-relative returns at 20, 40, and 60 trading days;
- triple-barrier result from next-day open;
- ambiguity and missing-bar flags;
- detector version and feature-specification version.

Research generation writes a new versioned artifact. It does not overwrite a
previous event table in place.

## Outcomes

### Continuous Outcomes

Primary continuous outcomes are 20-, 40-, and 60-trading-day buy-and-hold returns
relative to SPY, beginning at the next available open. Raw returns are retained
as diagnostics.

### Barrier Outcome

The secondary binary outcome records whether price reaches `+2 ATR` before
`-1 ATR` within the specified horizon. Entry is next-day open and ATR is frozen
using information available on the observation date. If both barriers are touched
within one daily bar, the result is `ambiguous`, not assigned optimistically.

## Statistical Analysis

Three primary specifications are preregistered:

1. VCP structure variables only;
2. momentum variables only;
3. VCP structure, momentum, and a small fixed set of VCP-by-momentum interactions.

Continuous outcomes use an interpretable robust linear model. Binary barrier
outcomes use logistic regression. Coefficients are standardized using training
data only. Missing values are imputed using training-fold statistics and paired
with missingness indicators where applicable.

Inference and validation include:

- standard errors clustered by observation date;
- purged chronological walk-forward evaluation with an embargo at least as long
  as the outcome horizon;
- date-block bootstrap confidence intervals;
- non-overlapping phase sensitivity analysis;
- same-ticker, similar-date, same-market-regime matched controls;
- comparison on a common observation set;
- explicit reporting of all attempted specifications and thresholds.

Multiple outcomes and factor families are reported with a false-discovery-rate
adjustment. Nominal p-values are never the sole acceptance criterion.

## Validation Gates

A factor is promoted from descriptive to research-supported only if all of these
conditions hold:

- its sign is stable across chronological test folds;
- its out-of-sample effect improves over the relevant nested baseline;
- the date-block confidence interval excludes zero in the preregistered primary
  outcome;
- the result survives the non-overlapping sensitivity analysis;
- its direction is consistent in matched-control analysis;
- costs and next-open execution do not reverse the portfolio-level implication;
- the result is not dependent on one ticker, sector, or short market episode.

Failure at any gate is recorded as a null result, not followed by threshold
searching on the same test period.

## Refactoring Boundaries

The current code mixes descriptive VCP factors with scoring and trading triggers.
The refactor will:

- preserve legacy scoring output until tests characterize it;
- add a new research detector rather than silently changing old backtest results;
- remove VCP from new predictive claims and new portfolio selection paths unless
  it later passes the validation gates;
- centralize calendar, next-open execution, ATR, and forward-outcome rules;
- separate event generation, feature generation, outcome generation, modeling,
  and reporting into independently tested modules.

No unrelated web UI or fundamental-data refactor is included.

## Testing Strategy

Synthetic OHLCV fixtures cover:

- a textbook sequence of decreasing contractions;
- increasing contractions that must be rejected;
- a monotonic rally that must not be called a base;
- a tight platform that must remain distinct from strict VCP;
- an active unconfirmed final leg;
- breakout, invalidation, expiry, and event deduplication;
- missing ticker bars and next-open timing;
- both barriers touched in the same daily bar;
- momentum windows and the one-month skip;
- cross-sectional ranking using only same-date information;
- folds whose training rows cannot see test-period outcomes.

A small set of real tickers, including current candidates and known false
positives, is retained as regression fixtures with frozen source dates. These
fixtures validate reproducibility, not profitability.

## Deliverables

1. Tested VCP structure detector with dated legs and explicit stages.
2. Historical event scanner and versioned event table.
3. Standard momentum feature module.
4. Leakage-safe outcome generator.
5. Preregistered regression and matched-control report.
6. Error gallery of accepted and rejected historical patterns.
7. A decision record stating which factors passed, failed, or remain
   underpowered.

## Known Limitations

- The local database contains roughly two years of history, so 12-month momentum
  leaves few independent market regimes.
- The ticker universe is not point-in-time and contains survivorship and selection
  bias.
- Daily bars cannot determine intraday barrier order.
- A deterministic VCP definition can be precise and reproducible without matching
  every discretionary chart reader.
- A larger point-in-time universe and longer history are required before any
  durable alpha claim.

