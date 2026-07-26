# Historical VCP and Pocket Pivot Entry Signals Design

## Goal

Replace ambiguous historical “unavailable” shape output with one causal,
auditable entry-signal history for Strict VCP, tight platforms, confirmed VCP
breakouts, and Pocket Pivots. The same Strict VCP definition must drive the
latest factor result, historical model cards, chart markers, and future
research evaluation.

This is a research signal system, not a trading recommendation.

## Scope

The implementation covers one selected stock at a time using local daily
OHLCV data. It produces point-in-time rows for every available trading date,
adds sparse chart annotations, and exposes evidence in the existing model
output panel.

The implementation does not add:

- intraday quote or trade data;
- portfolio or position-aware entry decisions;
- automatic order placement;
- a second competing VCP definition;
- future projected chart dates or lines.

## Canonical Definitions

### Strict VCP

`research.vcp.detect_vcp` becomes the canonical Strict VCP detector. All
historical calls pass only the OHLCV prefix ending on the observation date.

An accepted pattern exposes:

- pattern stage;
- base start and end;
- confirmed contraction legs with dates, prices, depth, and mean volume;
- pending unconfirmed leg, if present;
- Pivot and Pivot date;
- distance to Pivot;
- base depth, terminal range, contraction slope, last/first contraction
  ratio, adaptive ZigZag threshold, and volume dry-up ratio.

A rejected pattern exposes a typed rejection reason. Fewer than 60 sessions
is `insufficient_history`.

The existing `factors.compute.vcp_analysis` output is no longer the source of
truth for the `strict_vcp` factor. A compatibility adapter converts the
canonical `VCPPattern` into the factor dictionary currently consumed by the
API and factor table.

### Tight Platform

Tight platform remains a separate shape, not a relaxed Strict VCP.
`factors.compute.tight_platform` is evaluated on each historical prefix. The
result records active state, platform Pivot, range, volume dry-up, and typed
rejection reason.

### Confirmed VCP Breakout

A breakout on date `t` may use only a Strict VCP Pivot already visible before
`t`. A pattern first detected on `t` cannot retroactively create a breakout
on the same bar.

The signal requires all of:

1. previous close is at or below the known Pivot;
2. current close is above the known Pivot;
3. current volume divided by the mean of up to 50 prior sessions is at least
   `1.4`;
4. current close is no more than `5%` above the Pivot.

Price crossing, volume confirmation, buy-zone confirmation, volume ratio,
and percent above Pivot are stored separately. A breakout outside the
0–5% range is recorded as extended, not confirmed.

### Pocket Pivot

The observation date must close above the previous close. Its volume must
exceed the maximum volume among down days in the previous 10 complete
sessions. A down day is a session whose close is below its preceding
session’s close.

The signal is unavailable with fewer than 12 indexed sessions because the
first of the 10 comparison sessions also needs its preceding close. If the
prior 10-session window contains no down day, it is inactive with
`no_down_days_in_window`; an arbitrary up day must never become a Pocket
Pivot merely because the comparison set is empty.

The output records current volume, prior maximum down-day volume, comparison
window size, down-day count, and rejection reason.

## Components

### `research/entry_signals.py`

This module owns the pure sequential engine:

```python
ENTRY_SIGNAL_VERSION = "historical-entry-signals-v1"

def build_entry_signal_rows(history: pd.DataFrame) -> list[dict]:
    ...
```

The returned list has exactly one row per input date in ascending order.
Every row includes:

- `time`;
- `strict_vcp_active`, `strict_vcp_start`, `strict_vcp_stage`;
- `strict_vcp_pivot`, `strict_vcp_pivot_date`;
- `strict_vcp_reject_reason`, `strict_vcp_evidence`;
- `tight_platform_active`, `tight_platform_start`;
- `tight_platform_pivot`, `tight_platform_reject_reason`,
  `tight_platform_evidence`;
- `vcp_breakout_confirmed`, `vcp_breakout_price_confirmed`,
  `vcp_breakout_volume_confirmed`, `vcp_breakout_buy_zone_confirmed`;
- `vcp_breakout_pivot`, `vcp_breakout_volume_ratio`,
  `vcp_breakout_pct_over_pivot`, `vcp_breakout_reject_reason`;
- `pocket_pivot`, `pocket_pivot_current_volume`,
  `pocket_pivot_prior_down_volume`,
  `pocket_pivot_down_day_count`, `pocket_pivot_reject_reason`.

The engine owns one active, previously known Strict VCP event at a time. It
records first-seen state once, confirms a crossing from the known Pivot,
invalidates broken bases, and expires stale events using the existing event
lifetime semantics.

### `web/services/entry_signals.py`

A small service wraps the pure engine with a bounded in-memory LRU. Its cache
key includes:

- ticker;
- `ENTRY_SIGNAL_VERSION`;
- a deterministic fingerprint of the complete indexed OHLCV history.

Any append, correction, split-adjustment rewrite, or algorithm version change
is a cache miss. Returned rows are copied so request code cannot mutate the
cached artifact.

### Chart and factor integration

`build_chart_rows` remains responsible for ordinary price, volume, moving
average, reversal, and resistance fields. The stock endpoint asks
`EntrySignalService` for the selected ticker and merges entry rows by exact
ISO date.

The latest canonical Strict VCP row is adapted into the existing factor and
`structures.strict_vcp` contracts. Tight platform factor output keeps its
current contract. Latest and historical model outputs therefore consume the
same per-date fields.

Explicitly injected services remain supported in tests. The universe
endpoint does not run entry-signal history.

## Model Output Semantics

The bullish structure group contains production outputs for:

- Strict VCP;
- tight platform;
- confirmed VCP breakout;
- Pocket Pivot.

Each output uses:

- `active` when the rule fired;
- `inactive` when it was evaluated and did not fire;
- `unavailable` only for genuine missing evidence such as insufficient
  history.

The generic “historical shape not computed” state is removed for normal
chart dates. Model cards expose actual values, thresholds, conditions, and
typed rejection reasons. The existing planned demand-confirmation model
remains separate; it is not renamed to Pocket Pivot.

## Chart Annotations

Sparse annotations are generated from entry rows:

- blue diamond on `strict_vcp_start`;
- green upward arrow on `vcp_breakout_confirmed`;
- cyan circle on `pocket_pivot`.

Annotations use input trading dates only. They do not create forecast points,
future whitespace, price lines, or autoscale values. Multiple signals on one
date use deterministic vertical positions and concise labels. Existing
hover, lock, drag, zoom, and visible-range behavior must remain unchanged.

The latest active Pivot may remain a price line. Historical Pivots are shown
in the selected-date details rather than as many full-width lines.

## Performance

Entry history is calculated only for the selected stock. The universe list
continues to avoid heavy shapes.

Acceptance measurements include:

- current approximately two-year MRVL history;
- the longest locally available history;
- cold calculation;
- same-process cache hit;
- complete `/api/stocks/<ticker>` request.

The cache-hit target is under 50 milliseconds for the entry artifact. The
selected-stock warm request target remains under 2 seconds on the current
local dataset. If the longest cold scan exceeds 5 seconds, the implementation
must either optimize prefix evaluation or persist per-ticker entry artifacts
before closing the performance item.

## Error Handling

Malformed or non-finite OHLCV fails with the repository’s existing typed
market-data error; the engine does not silently manufacture inactive
signals. Missing history produces typed unavailable evidence.

An entry-cache failure falls back to direct pure calculation. Internal
exceptions are logged and continue to use the existing safe API error
envelope; paths and secrets are never returned.

## Testing

Pure engine tests cover:

- textbook decreasing contractions;
- non-decreasing contractions;
- insufficient history;
- first-seen state emitted once;
- a Pivot known before breakout;
- price-only breakout;
- volume-confirmed breakout;
- breakout more than 5% above Pivot;
- Pocket Pivot positive and negative cases;
- no down days in the prior window;
- tight platform active and rejected cases;
- exact one-row-per-date ordering;
- prefix invariance after future rows are appended.

Integration tests cover:

- latest factor and historical model output agreement;
- active, inactive, and unavailable status semantics;
- evidence and rejection-reason localization;
- API schema;
- sparse marker types and dates;
- no future chart points;
- unchanged drag, zoom, hover, and date-lock behavior;
- universe endpoint does not invoke the engine;
- cache hit, append invalidation, and historical correction invalidation.

Manual research checks cover NBIS, MU, AMD, MRVL, and RKLB without tuning
thresholds to those names.

## Acceptance Criteria

1. No available historical date displays Strict VCP or tight platform as
   “unavailable” merely because it was not computed.
2. Pocket Pivot is a production rule output with auditable volume evidence.
3. Latest and historical Strict VCP use one canonical detector.
4. Every historical result is reproducible from its OHLCV prefix.
5. Appending future data cannot change existing entry rows.
6. Chart annotations do not alter time range, price scale, dragging, zooming,
   hovering, or date locking.
7. Focused, frontend, performance, and complete test suites pass.
