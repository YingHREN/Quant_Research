# Market-Confirmed Direction Model Design

## Goal

Improve historical direction forecasts without tuning to NBIS by testing the
incremental value of stock, QQQ, sector, pressure, and early-reversal evidence
across the local universe. A feature is promoted to the live model only when it
improves leakage-safe out-of-sample results.

## Decisions

- Keep the existing Ridge forecast as the production baseline until a challenger
  passes the research gate.
- Train and evaluate on executable next-session-open to future-close returns.
- Use atomic observations rather than UI composite scores.
- Use expanding chronological folds. A training label must end strictly before
  the first test observation, so overlapping future returns cannot leak.
- Evaluate all locally available stocks for QQQ effects and separately report
  semiconductor/AI-infrastructure stocks for sector effects.
- Treat NBIS and AMD as regression cases, never as parameter-selection targets.
- Keep the current strict early-reversal alert as a sparse high-confidence event.
  Expose its four atomic conditions to the model so partial evidence can still
  contribute without fabricating a strict alert.

## Feature Families

### Stock

Existing trend, momentum, volatility, VCP, pressure, and reversal features.

### Market

Point-in-time QQQ trend state, QQQ distance from EMA20, QQQ 5-session return,
QQQ 20-session return, and QQQ volume ratio.

### Sector

For the semiconductor group, the causal SOXX/SMH composite trend state,
sector-versus-QQQ 20-session relative strength, and stock-versus-sector
20-session relative strength. Sector values remain unavailable for stocks that
are not mapped to this group.

### Early reversal

Prior-session high-volume selloff, current-session price acceptance, proximity
to the active descending resistance line, and current volume support. The model
receives these booleans, not the derived 0-100 display score.

## Challenger

The first challenger is a class-balanced logistic direction classifier for
`down`, `neutral`, and `up`. It is compared with:

1. the historical majority-class baseline;
2. the current Ridge direction;
3. stock-only logistic classification;
4. stock plus QQQ;
5. stock plus QQQ plus sector and early-reversal evidence.

Continuous fields are median-imputed and standardized using training-fold data
only. Missingness indicators are retained for optional sector fields. Model
selection is fixed before inspecting NBIS or AMD case rows.

## Metrics and Promotion Gate

Report 5-session and 20-session:

- coverage;
- balanced accuracy;
- down precision and recall;
- macro F1;
- confusion counts;
- mean next-open return by predicted direction;
- semiconductor/AI subgroup metrics;
- NBIS and AMD dated case predictions as diagnostics.

The challenger is eligible for production only if it improves mean
out-of-sample balanced accuracy and macro F1 over stock-only and majority
baselines, does not reduce down recall, and has at least 1,000 evaluated rows
with both up and down classes. If it fails, retain Ridge and publish the negative
result rather than changing the dashboard forecast.

## Data and Failure Handling

All data comes from the local SQLite history through 2026-07-23. Missing QQQ or
sector data remains missing and produces explicit coverage loss; it is never
forward-filled beyond the latest known observation or replaced by future data.
Rows without a next-session open or a mature horizon close are excluded from
evaluation.

## Verification

Unit tests prove feature causality, next-open target alignment, fold purging,
and missing-sector behavior. A deterministic local research command produces a
Markdown report and machine-readable CSV metrics. The full project test suite
must pass before any production-model change or merge.
