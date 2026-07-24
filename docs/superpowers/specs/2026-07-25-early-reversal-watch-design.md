# Early Reversal Watch Design

## Goal

Add a causal, end-of-session “early reversal watch” state that can identify
NBIS on 2026-07-17 before the existing structural reversal confirmation on
2026-07-20. The watch is an observation alert, not a confirmed reversal,
probability, trade instruction, or override of the Ridge forecast.

## Signal boundary

The early watch is separate from the existing three confirmed reversal
conditions:

- prior-high breakout;
- descending-trendline breakout;
- confirmed higher low.

The existing `reversal_signal_count` and `reversal_candidate` definitions do
not change. An early watch may appear while the confirmed count remains
`0/3`.

Every input must be observable by the selected session close. Appending future
bars must not change an existing watch score, condition, or explanation.

## Scoring definition

The watch has four atomic conditions worth 25 points each:

1. **Prior-session selloff:** the previous session returned at most `-5%`, its
   volume ratio was at least `1.2`, and its close-location value was at most
   `-0.5`.
2. **Current-session price acceptance:** the current close is above the
   previous close and the current close-location value is at least `0.0`.
3. **Near an unbroken descending trendline:** an active descending trendline
   exists, the current close has not closed above it, and the close is no more
   than `1%` below it.
4. **Current-session volume support:** the current volume ratio is at least
   `1.2`.

The score is the sum of the four atomic conditions.
`early_reversal_watch` is true only when the prior-session selloff and
current-session price-acceptance conditions are both true and the score is at
least `75`. At least one of trendline proximity or volume support must
therefore also be present. A qualifying signal exposes the stable condition
codes that contributed to its score.

The first version deliberately does not use a forward return, future pivot, or
future structural confirmation. It also does not claim that a score of 100 is
a 100% reversal probability.

## Architecture

Create a focused research module that consumes a validated OHLCV history and
the causal rows returned by `build_reversal_rows`. It returns one early-watch
row per input session containing:

- `early_reversal_score`;
- `early_reversal_watch`;
- `early_reversal_conditions`;
- the four atomic boolean fields used to reproduce the score.

The module reuses the existing market-pressure definitions for volume ratio
and close location. It must not duplicate or subtly alter those formulas.

The stock-detail chart adapter adds the scalar fields and condition codes to
each chart row. The factor registry exposes the score as a structure factor
with concise Chinese and English methodology. The Ridge feature registry and
direction-adjustment policy do not consume this signal in this change.

## User interface

When `early_reversal_watch` is true:

- add a conspicuous amber marker above the selected candle;
- label it “早期反转观察 · {score}” in Chinese and
  “Early reversal watch · {score}” in English;
- show the score and the contributing conditions in the selected-date detail
  panel;
- explain that it is an end-of-session observation awaiting structural
  confirmation.

The marker must coexist with forecast and confirmed-reversal markers. It must
not add future chart dates, alter the visible time range, or move the locked
crosshair.

## NBIS acceptance case

Using only data through 2026-07-17:

- the 2026-07-16 selloff satisfies the prior-session selloff condition;
- the 2026-07-17 close above the prior close with positive close location
  satisfies current-session price acceptance;
- the 2026-07-17 close of `177.71` is less than `1%` below the causal
  descending trendline near `179.28`;
- the 2026-07-17 volume ratio near `1.34` satisfies volume support.

The expected early-watch score is `100`, `early_reversal_watch` is true, and
the confirmed reversal count remains `0/3`. On 2026-07-20 the existing
trendline-breakout condition provides the later structural confirmation.

## Testing

Research unit tests cover:

- the exact four-condition score and inclusive thresholds;
- absence when the prior-session selloff or current price acceptance is
  missing;
- an early watch at `75` points when exactly one supporting condition is
  present;
- future-row append invariance;
- no mutation of input history or reversal rows.

Web tests cover:

- stable chart-row fields and factor metadata;
- localized marker and detail explanations;
- coexistence with the existing `0/3` confirmed-reversal state;
- no forecast-direction adjustment;
- no chart timeline or locked-date movement.

Browser acceptance checks NBIS on 2026-07-17 and 2026-07-20 in both Chinese
and English.
