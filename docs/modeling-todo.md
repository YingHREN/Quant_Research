# Quant Dashboard Modeling TODO

This is the persistent backlog for the VCP, forecast, reversal, supply/demand,
market-context, and data-source work discussed for the local dashboard.

## P0 — Early reversal observation

- [x] Implement the causal early-reversal watch defined in
  `docs/superpowers/specs/2026-07-25-early-reversal-watch-design.md`.
- [x] Detect the NBIS 2026-07-17 early observation point using only data
  available through that close.
- [x] Score prior-session selloff, current-session price acceptance,
  descending-trendline proximity, and current volume support.
- [x] Keep early observation separate from confirmed reversal conditions.
- [ ] Distinguish intraday break, closing confirmation, and next-session
  confirmation.
- [x] Add localized chart markers, date details, factor explanations, and
  scores.
- [x] Compare entry after the 2026-07-17 observation with entry after the
  2026-07-20 confirmation using next-open execution.

## P0 — Repair the five-session forecast

- [ ] Stop presenting the five-session Ridge result as reliable direction
  while it does not beat simple baselines.
- [ ] Show zero-return, historical-mean, and always-up baselines beside model
  evaluation.
- [ ] Display “no demonstrated forecast advantage” when walk-forward results
  do not beat the configured baseline.
- [x] Replace the five-session return-to-direction conversion with a directly
  evaluated classification objective.
- [x] Use next-session open as the executable entry price.
- [ ] Evaluate non-overlapping five-session outcomes in addition to the daily
  overlapping research sample.
- [ ] Add time decay and ticker/sector hierarchy experiments. Class-balanced
  logistic and shallow boosted challengers were tested on 2026-07-25.
- [x] Evaluate the causal multi-scale recency-weighted momentum challenger
  defined in
  `docs/superpowers/specs/2026-07-25-recency-weighted-momentum-design.md`.
- [x] Compare decay-only, decay-plus-volume, and decay-plus-market-context
  ablations before considering a causal-attention sequence model. The
  2026-07-25 challenger was not promoted: the best direction classifier
  improved balanced accuracy but its predicted-down bucket had positive
  realized return and it did not correct the MU/NBIS false-bull cases.
- [x] Report NBIS, semiconductor, and full-universe results separately.
- [x] Re-evaluate the bearish-risk override independently; do not retain an
  override that reduces out-of-sample accuracy.
- [ ] Require stable walk-forward improvement before restoring a prominent
  direction label.

## P1 — Separate forecast and risk semantics

- [ ] Display the raw Ridge return forecast independently from short-term
  bearish-turn risk.
- [ ] Do not silently replace a positive predicted return with a negative
  direction label.
- [ ] Add a combined state such as “medium-term positive, short-term high
  risk” without claiming a probability.
- [ ] Label every signal as intraday, close-confirmed, or next-session
  executable.
- [ ] Show each bearish condition’s actual value, threshold, and points.
- [ ] Separate literal failed breakouts from the current composite
  pivot-distance risk proxy.
- [ ] Reduce correlated double-counting among distribution, abnormal volume,
  volume expansion, weak close, and signed-volume pressure.
- [ ] Use horizon-specific definitions and weights for 5, 20, and 60 sessions.
- [ ] Rename “反转候选” to “结构转强” and show the exact satisfied conditions.
- [ ] Preserve the distinction between an event-day signal and a persistent
  structural state.

## P1 — Selling pressure during an uptrend

- [ ] Define the eligible uptrend regime using moving-average slopes,
  higher-high/higher-low structure, and relative strength versus QQQ and the
  sector proxy.
- [ ] Add high-volume non-progress:
  `volume_ratio >= 1.5` and `abs(daily_return) <= 0.5%`.
- [ ] Add price-progress efficiency:
  `abs(daily_return) / volume_ratio`.
- [ ] Add upper-wick rejection using wick/range, close location, resistance
  proximity, and volume support.
- [ ] Add literal failed-breakout detection: intraday high above prior
  resistance followed by a close back below resistance.
- [ ] Measure repeated resistance tests with rising volume but declining price
  progress.
- [ ] Count persistent distribution sessions over rolling 5-, 10-, and
  20-session windows.
- [ ] Detect relative-strength deterioration before absolute price breakdown.
- [ ] Add volume-confirmed EMA20 and higher-low breakdown states.
- [ ] Detect weak, low-volume rebounds that fail to recover broken support.
- [ ] Build an independent `supply_pressure_score`; do not define it as the
  inverse of buying pressure.
- [ ] Group correlated evidence before scoring so one high-volume down candle
  cannot receive multiple full weights for the same information.

## P1 — Buying pressure and demand confirmation

- [ ] Add a close-location × volume-ratio daily buying/selling proxy.
- [ ] Add strong-close and up-volume participation conditions.
- [ ] Require closing acceptance and follow-through after resistance
  breakouts.
- [ ] Detect seller exhaustion: extreme sell volume without further downside.
- [ ] Detect buyer absorption: high inferred selling pressure while price
  holds or recovers.
- [ ] Detect low-volume pullbacks followed by a confirmed higher low.
- [ ] Add positive relative-strength turns versus sector and QQQ.
- [ ] Build an independent `demand_confirmation_score`.
- [ ] Display four states: healthy advance, two-sided high-volume battle,
  distribution risk, and low-participation consolidation.
- [ ] Backtest supply and demand scores independently and jointly.

## P2 — Trained reversal and pressure models

- [x] Use the available atomic bearish and early-reversal conditions as
  model features rather than treating hand-set scores as probabilities.
- [x] Train separate future-5-session and future-20-session direction
  classifiers; path-dependent downside classifiers remain separate follow-up.
- [x] Compare raw Ridge, rules-only, logistic classification, and shallow
  boosted classification. The rules-only bearish override had no full-universe
  lift and must not be treated as a probability.
- [x] Use expanding-window walk-forward evaluation with purged
  label boundaries.
- [ ] Report precision, recall, balanced accuracy, ROC AUC, PR AUC, coverage,
  return, maximum drawdown, and turnover.
- [ ] Calibrate probabilities only when sample and both-class requirements are
  met.
- [x] Study high-volatility semiconductor and AI-infrastructure stocks
  separately.
- [x] Maintain named NBIS and AMD event-case regressions without
  training specifically to those dates.
- [x] Measure incremental value from QQQ, sector, and stock-level agreement or
  divergence.
- [ ] Add time-decayed samples and a ticker/sector hierarchy without one-hot
  memorization, then repeat the fixed promotion gate.
- [ ] Train the path-dependent maximum-adverse-excursion classifier separately
  from terminal direction; do not reuse terminal-return labels as “risk.”

## P2 — Intraday trades and quotes

- [ ] Preserve the provider-neutral broker interface for later full-market
  trade and quote sources.
- [ ] Infer aggressor side from trades and prevailing bid/ask quotes using a
  tested Lee–Ready-style classifier.
- [ ] Calculate buyer-initiated volume, seller-initiated volume, trade delta,
  and cumulative volume delta.
- [ ] Calculate top-of-book imbalance and order-flow imbalance from quote
  updates, executions, additions, and cancellations.
- [ ] Detect seller absorption: aggressive buying without corresponding
  upward price progress.
- [ ] Detect buyer absorption: aggressive selling without corresponding
  downward price progress.
- [ ] Add price impact per unit of signed flow and depth-normalized order-flow
  imbalance.
- [ ] Detect price/flow divergence, replenishing ask liquidity, and vanishing
  bid liquidity.
- [ ] Record coverage, venue, quote latency, correction/cancel handling, and
  feed limitations with every intraday score.
- [ ] Keep IEX-only or partial-market results visibly distinct from
  consolidated-market results.

## P3 — Data and interface extensions

- [ ] Continue supporting free daily OHLCV as the minimum viable data layer.
- [ ] Evaluate authenticated free intraday trades and quotes before purchasing
  consolidated historical feeds.
- [ ] Keep Alpaca, Futu, and future broker adapters behind the provider-neutral
  market-data contracts.
- [ ] Add a full-market TAQ-compatible adapter only when licensing and storage
  requirements are defined.
- [ ] Expose factor registration interfaces so new atomic evidence, scores,
  explanations, and evaluation results can be added without chart rewrites.

## Completed foundations

- [x] Add VCP, momentum, reversal, market-context, and pressure foundations.
- [x] Add prior-high breakout, descending-trendline breakout, and confirmed
  higher-low events.
- [x] Add the first bearish-turn risk score and expose its atomic conditions.
- [x] Preserve raw forecast direction and direction-adjustment provenance.
- [x] Add localized factor explanations and score panels.
- [x] Add linked price/volume charts with locked historical inspection.
- [x] Prevent prediction updates and pointer gestures from moving the chart
  timeline.
- [x] Write and commit the early-reversal-watch design specification.
