# Market-confirmed direction ablation

- Data through: 2026-07-23
- Local universe: 194 tickers
- Execution label: enter at the next-session open and exit at the 5th/20th future session close.
- Validation: expanding chronological folds with exact label-end purging.
- NBIS and AMD are diagnostics only and are not used to select parameters.

## Promotion decision

**DO NOT PROMOTE** — boosted_full_context: 5d_down_recall_degraded, 20d_down_recall_degraded

## Out-of-sample metrics

| scope | horizon | specification | sample_count | coverage | balanced_accuracy | macro_f1 | down_precision | down_recall | mean_return_predicted_down | mean_return_predicted_neutral | mean_return_predicted_up |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 5.0000 | boosted_full_context | 76747.0000 | 1.0000 | 0.3746 | 0.3607 | 0.3662 | 0.3120 | 0.0082 | 0.0025 | 0.0025 |
| all | 5.0000 | full_context | 76747.0000 | 1.0000 | 0.3459 | 0.3467 | 0.3562 | 0.4151 | 0.0083 | 0.0010 | 0.0012 |
| all | 5.0000 | majority_baseline | 76747.0000 | 1.0000 | 0.3333 | 0.2020 | 0.0000 | 0.0000 |  |  | 0.0043 |
| all | 5.0000 | ridge_baseline | 76747.0000 | 1.0000 | 0.3273 | 0.3112 | 0.3341 | 0.2632 | 0.0073 | 0.0050 | 0.0007 |
| all | 5.0000 | stock_only | 76747.0000 | 1.0000 | 0.3603 | 0.3582 | 0.3600 | 0.4200 | 0.0075 | 0.0018 | 0.0019 |
| all | 5.0000 | stock_qqq | 76747.0000 | 1.0000 | 0.3445 | 0.3450 | 0.3544 | 0.4132 | 0.0086 | 0.0013 | 0.0009 |
| all | 5.0000 | stock_qqq_early | 76747.0000 | 1.0000 | 0.3446 | 0.3449 | 0.3549 | 0.4142 | 0.0085 | 0.0012 | 0.0010 |
| all | 20.0000 | boosted_full_context | 73837.0000 | 1.0000 | 0.3528 | 0.3396 | 0.3231 | 0.3244 | 0.0307 | 0.0129 | 0.0154 |
| all | 20.0000 | full_context | 73837.0000 | 1.0000 | 0.3218 | 0.3244 | 0.2980 | 0.3185 | 0.0308 | 0.0157 | 0.0131 |
| all | 20.0000 | majority_baseline | 73837.0000 | 1.0000 | 0.3333 | 0.2109 | 0.0000 | 0.0000 |  |  | 0.0202 |
| all | 20.0000 | ridge_baseline | 73837.0000 | 1.0000 | 0.3066 | 0.3007 | 0.2646 | 0.2062 | 0.0329 | 0.0216 | 0.0125 |
| all | 20.0000 | stock_only | 73837.0000 | 1.0000 | 0.3232 | 0.3239 | 0.3134 | 0.4207 | 0.0297 | 0.0133 | 0.0108 |
| all | 20.0000 | stock_qqq | 73837.0000 | 1.0000 | 0.3165 | 0.3193 | 0.2904 | 0.3137 | 0.0328 | 0.0154 | 0.0115 |
| all | 20.0000 | stock_qqq_early | 73837.0000 | 1.0000 | 0.3166 | 0.3194 | 0.2906 | 0.3137 | 0.0326 | 0.0157 | 0.0116 |
| semiconductor_ai | 5.0000 | boosted_full_context | 8756.0000 | 1.0000 | 0.3204 | 0.3204 | 0.3507 | 0.3808 | 0.0191 | 0.0145 | 0.0057 |
| semiconductor_ai | 5.0000 | full_context | 8756.0000 | 1.0000 | 0.3269 | 0.3214 | 0.3646 | 0.4714 | 0.0159 | 0.0094 | 0.0088 |
| semiconductor_ai | 5.0000 | majority_baseline | 8756.0000 | 1.0000 | 0.3308 | 0.3071 | 0.3720 | 0.4921 | 0.0097 |  | 0.0150 |
| semiconductor_ai | 5.0000 | ridge_baseline | 8756.0000 | 1.0000 | 0.3224 | 0.3195 | 0.3596 | 0.4215 | 0.0154 | 0.0154 | 0.0075 |
| semiconductor_ai | 5.0000 | stock_only | 8756.0000 | 1.0000 | 0.3269 | 0.3193 | 0.3630 | 0.3928 | 0.0110 | 0.0151 | 0.0131 |
| semiconductor_ai | 5.0000 | stock_qqq | 8756.0000 | 1.0000 | 0.3157 | 0.3062 | 0.3518 | 0.4017 | 0.0181 | 0.0202 | 0.0071 |
| semiconductor_ai | 5.0000 | stock_qqq_early | 8756.0000 | 1.0000 | 0.3160 | 0.3072 | 0.3510 | 0.3999 | 0.0183 | 0.0215 | 0.0069 |
| semiconductor_ai | 20.0000 | boosted_full_context | 8426.0000 | 1.0000 | 0.3542 | 0.3534 | 0.3464 | 0.3956 | 0.0465 | 0.0237 | 0.0787 |
| semiconductor_ai | 20.0000 | full_context | 8426.0000 | 1.0000 | 0.3492 | 0.3441 | 0.3498 | 0.4673 | 0.0400 | 0.0677 | 0.0776 |
| semiconductor_ai | 20.0000 | majority_baseline | 8426.0000 | 1.0000 | 0.3417 | 0.3155 | 0.3501 | 0.5386 | 0.0422 |  | 0.0795 |
| semiconductor_ai | 20.0000 | ridge_baseline | 8426.0000 | 1.0000 | 0.3487 | 0.3466 | 0.3417 | 0.3754 | 0.0451 | 0.0300 | 0.0808 |
| semiconductor_ai | 20.0000 | stock_only | 8426.0000 | 1.0000 | 0.3401 | 0.3175 | 0.3484 | 0.5066 | 0.0447 | -0.0113 | 0.0756 |
| semiconductor_ai | 20.0000 | stock_qqq | 8426.0000 | 1.0000 | 0.3323 | 0.3260 | 0.3241 | 0.3800 | 0.0481 | 0.0141 | 0.0702 |
| semiconductor_ai | 20.0000 | stock_qqq_early | 8426.0000 | 1.0000 | 0.3311 | 0.3244 | 0.3231 | 0.3796 | 0.0484 | 0.0118 | 0.0701 |

## NBIS and AMD diagnostics

| scope | ticker | observation_date | horizon | fold | specification | actual_return | actual_direction | predicted_direction | training_samples | predicted_return |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | majority_baseline | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | stock_only | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | stock_qqq | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | stock_qqq_early | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | full_context | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | ridge_baseline | -0.0721 | down | up | 76666.0000 | 0.0363 |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | boosted_full_context | -0.0721 | down | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | majority_baseline | 0.0159 | up | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | stock_only | 0.0159 | up | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | stock_qqq | 0.0159 | up | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | stock_qqq_early | 0.0159 | up | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | full_context | 0.0159 | up | up | 76666.0000 |  |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | ridge_baseline | 0.0159 | up | up | 76666.0000 | 0.0216 |
| all | AMD | 2026-07-01 | 5.0000 | 4.0000 | boosted_full_context | 0.0159 | up | up | 76666.0000 |  |

## Interpretation

Balanced accuracy and macro F1 are the primary anti-bias metrics. Down recall measures whether the model actually identifies falling periods rather than defaulting to the market's positive base rate. Sector evidence is valid only for the semiconductor and AI infrastructure subgroup.

The shallow boosted model is the strongest challenger on aggregate balanced
accuracy (5 sessions: 0.3746; 20 sessions: 0.3528), but it reduces down recall
versus the stock-only classifier (0.3120 versus 0.4200 at 5 sessions; 0.3244
versus 0.4207 at 20 sessions). It also still labels NBIS on 2026-07-01 as
`up`, so aggregate improvement does not solve the named failure.

The existing rules-only bearish score was evaluated independently. At the
70-point threshold its 5-session terminal-down precision was 35.4% versus a
36.9% unconditional base rate. For next-open maximum adverse excursion, its
precision was 45.0% versus a 45.7% base rate. The 100-point drawdown result
rose only to 46.6% at 0.6% coverage. This confirms that the current override
is a descriptive risk flag, not a validated direction probability.
