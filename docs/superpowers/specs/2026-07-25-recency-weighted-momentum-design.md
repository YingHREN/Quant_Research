# Recency-Weighted Momentum Challenger Design

## Goal

Test whether a causal, recency-weighted momentum representation improves the
five-session executable direction forecast without promoting an unproven model
to the dashboard.

The experiment must answer three separate questions:

1. Does recency weighting add information beyond the existing skipped
   3–1, 6–1, and 12–1 momentum factors?
2. Does conditioning recent momentum on stock volume, QQQ, and sector context
   reduce false bullish forecasts around distribution and trend breaks?
3. Are any gains stable in the full universe, semiconductor/AI-infrastructure
   stocks, and named MU/NBIS event cases?

## Scope

This iteration is an interpretable challenger, not a Transformer deployment.
It adds deterministic multi-scale temporal features and lets the existing
regularized models learn their signs and magnitudes. A causal-attention model is
deferred until this smaller feature set demonstrates incremental out-of-sample
value.

No dashboard prediction, production factor list, or forecast label changes in
this experiment.

## Feature design

For daily close-to-close log return \(r_{t-k}\), define a normalized
exponentially decayed value over a causal lag interval:

\[
M(a,b,\tau)=
\frac{\sum_{k=a}^{b}\exp(-(k-a)/\tau)r_{t-k}}
{\sum_{k=a}^{b}\exp(-(k-a)/\tau)}
\]

The initial scales are:

- `decay_mom_1_3`: lags 1–3, half-life 2 sessions; shock/reversal head.
- `decay_mom_4_10`: lags 4–10, half-life 4 sessions; primary near-term head.
- `decay_mom_11_20`: lags 11–20, half-life 7 sessions; swing head.
- `decay_mom_21_60`: lags 21–60, half-life 20 sessions; regime head.
- `decay_mom_1_20`: lags 1–20, half-life 7 sessions; compact baseline.

Each value is annualization-free and preserves sign. Missing history remains
missing rather than being replaced with zero inside feature computation.

Context features are calculated over the same 1–20-session causal window:

- `decay_volume_confirmation_1_20`: decayed return signed by normalized volume;
  positive for price progress with participation and negative for distribution.
- `decay_close_location_pressure_1_20`: decayed close-location value multiplied
  by normalized volume; separates strong and weak closes.
- `decay_excess_qqq_1_20`: stock decayed momentum minus QQQ momentum.
- `decay_excess_sector_1_20`: stock decayed momentum minus sector momentum.
- `decay_market_agreement_1_20`: stock, QQQ, and sector sign agreement, weighted
  by the magnitude of stock momentum.

Volume ratios must use only information available through the observation
date. Rolling baselines must be shifted where necessary so a session is not
used to define its own expected volume.

## Model comparisons

All challengers use the same observations, executable targets, folds, and
training-only preprocessing.

1. `ridge_current`: the frozen production research feature set.
2. `ridge_decay_only`: current set plus the five decayed momentum heads.
3. `ridge_decay_volume`: decay features plus volume and close-location
   confirmation.
4. `ridge_decay_market`: all proposed decay, volume, QQQ, and sector features.
5. `logistic_decay_market`: direct direction challenger using the full proposed
   feature set.

The first implementation remains linear and regularized. This makes learned
signs inspectable and prevents a small nonlinear model from being confused with
evidence for the temporal representation itself.

## Leakage controls

- Features at date \(t\) may use observations only through the close at \(t\).
- The executable entry is the next session open.
- A five-session label exits at the fifth future close.
- Expanding folds purge every training row whose label end is not strictly
  before the test fold.
- Normalization, clipping thresholds, and imputation are fit on training data
  only.
- Overlapping daily labels and a non-overlapping five-session evaluation are
  both reported.
- Corporate-action-adjusted OHLCV must remain internally consistent.

## Evaluation

Report, per specification and fold:

- balanced direction accuracy and ordinary direction accuracy;
- precision/recall for bearish and bullish classes;
- MAE of predicted return for Ridge specifications;
- rank IC where predictions are continuous;
- coverage and class distribution;
- mean executable return by predicted class;
- worst future-five-session return and maximum adverse excursion when
  available.

Also report results for:

- full universe;
- semiconductor/AI-infrastructure subset;
- high-volatility subset;
- MU and NBIS event timelines, including dates already discussed.

## Promotion gate

The challenger is not eligible for production merely because aggregate accuracy
increases. It must:

1. beat `ridge_current` and the always-up/majority baseline on the primary
   five-session direction metric;
2. improve or preserve bearish recall without a material collapse in bullish
   precision;
3. improve in a majority of eligible walk-forward folds;
4. avoid material degradation in the semiconductor subset;
5. reduce, or at least not worsen, the known MU/NBIS false-bullish cases;
6. remain stable when the latest calendar segment is held out;
7. show an incremental benefit from decay features in an ablation, rather than
   only from unrelated context features.

Failure is a valid result. In that case the experiment and evidence are kept,
but the online forecast remains unchanged.

## Future causal-attention experiment

A learned attention model may follow only after this promotion gate is met. It
would consume a 60-session sequence with causal masking and separate short- and
medium-horizon heads. Attention weights would be treated as diagnostics, not
causal explanations, and compared with permutation or gradient-based feature
importance.
