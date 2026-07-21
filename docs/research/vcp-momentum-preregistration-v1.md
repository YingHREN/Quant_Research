# VCP Momentum Preregistration v1

Frozen before inspecting model results on `vcp_events_v1.csv`.

## Data and observation

- Primary stage: first `near_pivot` observation for each unique VCP base.
- Signal information ends at date `t`; entry begins at the next actual open.
- Duplicate event identities are prohibited.
- Same-bar upper/lower barrier touches are `ambiguous` and excluded from binary analysis.

## Outcomes

- Primary: 40-trading-day return relative to SPY (`rel_ret_40`).
- Secondary: `rel_ret_20`, `rel_ret_60`, and `barrier_label` (`up` versus `down`).
- Secondary outcomes cannot rescue a failed primary result.

## Feature families

VCP-only:

- `n_legs`
- `last_first_ratio`
- `contraction_slope`
- `terminal_range_pct`
- `volume_dryup_ratio`
- `distance_to_pivot_pct`
- `base_depth_pct`

Momentum-only:

- `mom_3_1_rank`
- `mom_6_1_rank`
- `mom_12_1_rank`
- `ret_1m`
- `excess_mom_6_1`
- `vol_adjusted_mom_6_1`

The combined model contains both families and exactly these interactions:

- `last_first_ratio × mom_6_1_rank`
- `volume_dryup_ratio × mom_6_1_rank`
- `terminal_range_pct × mom_12_1_rank`

No feature or interaction may be added after viewing v1 results.

## Models and validation

- Continuous model: Ridge regression, `alpha=1.0`, `solver=lsqr`.
- Five chronological folds with expanding training history.
- Embargo: 40 business days before each test fold for the primary target.
- Missing values, means, and scales are learned from the training fold only.
- All three specifications use identical test observations.
- Date-block bootstrap: block length 40 dates, 2,000 draws, seed 42.
- Non-overlapping phase and matched-control checks are required for a `PASS`.
- Multiple comparisons use Benjamini-Hochberg false-discovery-rate adjustment.

## Decision rule

Report each family as `PASS`, `FAIL`, or `UNDERPOWERED`.

`PASS` requires all of the following on the primary outcome:

1. positive out-of-sample incremental performance over the nested baseline;
2. effect direction stable in at least 70% of eligible test folds;
3. date-block 95% confidence interval excluding zero;
4. consistent direction under non-overlapping observations;
5. consistent direction against same-ticker, same-regime matched controls;
6. no dependence on one ticker or a single short market episode;
7. enough independent time coverage to include multiple market regimes.

If the dataset cannot run a required gate, the result is `UNDERPOWERED`, not
`PASS`. Failed gates are not followed by threshold searches on the same data.

