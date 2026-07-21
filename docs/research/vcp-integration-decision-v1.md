# VCP Integration Decision v1

## Decision

Keep the new VCP detector and its fields in research and descriptive monitoring
only. Do not integrate it into portfolio selection or describe it as a validated
buy signal.

## Evidence

The v1 event table contains 328 unique near-pivot events across 127 tickers and
164 independent observation dates from 2025-07-24 through 2026-07-17.

On the preregistered primary 40-day SPY-relative outcome:

- VCP-only mean fold correlation: -0.026;
- VCP-only mean out-of-sample R-squared: -0.580;
- VCP-plus-momentum mean fold correlation: +0.004;
- VCP-plus-momentum mean out-of-sample R-squared: -0.380;
- the combined model's date-block interval for MSE improvement is entirely
  negative.

These results do not support VCP structure as an incremental predictor. The
sample also lacks the history, matched controls, sector metadata, and independent
market regimes required for a conclusive test, so the formal verdict is
`UNDERPOWERED`, not `PASS`.

The momentum-only model is a follow-up research hypothesis, not an integration
candidate. It produced positive mean fold correlations at 40 and 60 days, but
the primary 40-day block-bootstrap interval crosses zero. The most stable
individual coefficient was `mom_12_1_rank` (positive in 4/4 training folds).
`vol_adjusted_mom_6_1` was negative in 4/4 folds. Both require longer data and
the missing validation gates.

## Known legacy conflict

`scoring/engine.py` currently:

- allocates up to 20 points to VCP structure;
- rescales those points upward in `price_only=True` mode;
- emits `vcp_breakout` and `buyable_now` triggers;
- feeds the resulting score to `engine_v2.py` selection.

Characterization tests preserve this legacy behavior so that later changes are
explicit. This iteration does not silently change historical backtest behavior.

## Required follow-up before changing the legacy engine

1. Build point-in-time daily snapshots for same-ticker, same-regime controls.
2. Extend the price history to at least ten years and use a point-in-time universe.
3. Re-run the frozen v1 definitions without tuning on the new history.
4. If VCP remains unsupported, remove VCP points and `buyable_now` language from
   new selection paths in a separately approved change.
5. If momentum survives every gate, introduce it in shadow mode before allocating
   capital.

