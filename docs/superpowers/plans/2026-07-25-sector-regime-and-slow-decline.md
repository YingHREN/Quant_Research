# Sector Regime and Slow-Decline Implementation Plan

**Goal:** Add causal sector-stress and persistent slow-decline state models so
the unified forecast decision can detect broad semiconductor selloffs and
software-style erosion that a terminal-return Ridge forecast misses.

**Architecture:** Keep Ridge unchanged. Build two independent, deterministic
state frames from daily OHLCV: a group regime model shared by explicitly
mapped constituents, and a stock slow-decline model. Both use only data
available through each row, retain raw score plus exponentially decayed memory,
and enter the decision layer as named risk sources. The combined policy uses
the maximum remembered source score and records the source; no score is called
a probability.

## Task 1: Causal group stress

- Add `research/group_regime.py`.
- Measure five-session group and relative returns, breadth above EMA20,
  down-volume breadth, distribution breadth, new-20-low breadth, and mean
  volume ratio.
- Score only when at least 80% of weighted evidence is available.
- Apply the existing causal 5-session half-life / 10-session memory.
- Prove future appends cannot change earlier rows.

## Task 2: Software group and generalized market features

- Add IGV and XSW as reference tickers and an explicit software constituent
  map including ADBE.
- Build atomic sector features per modeled group rather than hard-coding
  semiconductor context for every ticker.
- Reject duplicate ticker membership.
- Leave software sector context unavailable when its reference series are
  absent.

## Task 3: Persistent slow decline

- Add `research/slow_decline.py`.
- Score 20/60-session negative return, price below falling EMA20, price below
  SMA50, negative five-session momentum, weak rebound participation,
  persistent distribution, and underperformance versus group and QQQ.
- Separate the raw daily score from its remembered state.
- Add ADBE-like synthetic regression and future-data invariance tests.

## Task 4: Unified decision sources

- Extend risk context with individual, group, and slow-decline state fields.
- Select the highest remembered score and record all tied/active source codes.
- Preserve the existing immediate override precedence.
- Render concise localized source names and source scores in forecast detail.

## Task 5: Point-in-time evaluation and integration

- Evaluate source availability, coverage, downside precision/recall, balanced
  accuracy, ROC AUC when both classes exist, future maximum adverse excursion,
  and terminal returns with purged label boundaries.
- Include MU, INTC, NBIS, MRVL, and ADBE event regressions without tuning
  thresholds to individual dates.
- Run the complete Python and Node-backed test suite, inspect the live
  dashboard in the browser, and merge the verified branch to `main`.
