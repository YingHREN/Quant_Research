# Recency-weighted momentum ablation

- Data through: 2026-07-23
- Local universe: 194 tickers
- Status: offline research challenger; production forecasts are unchanged.
- Execution label: enter at the next-session open and exit at the fifth future session close.
- Validation: expanding chronological folds with exact label-end purging.

## Promotion decision

**DO NOT PROMOTE** — logistic_decay_market: predicted_down_return_not_negative, predicted_class_returns_not_ordered, known_false_bull_not_corrected

## Full-universe and semiconductor_ai metrics

| scope | horizon | evaluation_mode | fold | specification | sample_count | coverage | balanced_accuracy | macro_f1 | down_precision | down_recall | mean_return_predicted_down | mean_return_predicted_neutral | mean_return_predicted_up |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 5.0000 | overlapping | 0.0000 | logistic_decay_market | 76747.0000 | 1.0000 | 0.3558 | 0.3580 | 0.3582 | 0.3887 | 0.0079 | 0.0001 | 0.0024 |
| all | 5.0000 | overlapping | 0.0000 | majority_baseline | 76747.0000 | 1.0000 | 0.3333 | 0.2020 | 0.0000 | 0.0000 |  |  | 0.0043 |
| all | 5.0000 | overlapping | 0.0000 | ridge_current | 76747.0000 | 1.0000 | 0.3336 | 0.2843 | 0.3176 | 0.1341 | 0.0066 | 0.0051 | 0.0017 |
| all | 5.0000 | overlapping | 0.0000 | ridge_decay_market | 76747.0000 | 1.0000 | 0.3298 | 0.2955 | 0.3212 | 0.1897 | 0.0071 | 0.0047 | 0.0015 |
| all | 5.0000 | overlapping | 0.0000 | ridge_decay_only | 76747.0000 | 1.0000 | 0.3309 | 0.2850 | 0.3096 | 0.1404 | 0.0072 | 0.0051 | 0.0015 |
| all | 5.0000 | overlapping | 0.0000 | ridge_decay_volume | 76747.0000 | 1.0000 | 0.3287 | 0.2872 | 0.3088 | 0.1508 | 0.0072 | 0.0049 | 0.0017 |
| all | 5.0000 | overlapping | 1.0000 | logistic_decay_market | 19594.0000 | 1.0000 | 0.3657 | 0.3477 | 0.4340 | 0.2562 | 0.0006 | -0.0021 | 0.0007 |
| all | 5.0000 | overlapping | 1.0000 | majority_baseline | 19594.0000 | 1.0000 | 0.3333 | 0.2013 | 0.0000 | 0.0000 |  |  | 0.0005 |
| all | 5.0000 | overlapping | 1.0000 | ridge_current | 19594.0000 | 1.0000 | 0.3589 | 0.2902 | 0.4243 | 0.0382 | -0.0045 | -0.0001 | 0.0010 |
| all | 5.0000 | overlapping | 1.0000 | ridge_decay_market | 19594.0000 | 1.0000 | 0.3575 | 0.2971 | 0.4002 | 0.0557 | 0.0016 | -0.0012 | 0.0010 |
| all | 5.0000 | overlapping | 1.0000 | ridge_decay_only | 19594.0000 | 1.0000 | 0.3564 | 0.2906 | 0.3834 | 0.0424 | -0.0017 | -0.0008 | 0.0011 |
| all | 5.0000 | overlapping | 1.0000 | ridge_decay_volume | 19594.0000 | 1.0000 | 0.3566 | 0.2895 | 0.3964 | 0.0399 | -0.0019 | -0.0004 | 0.0009 |
| all | 5.0000 | overlapping | 2.0000 | logistic_decay_market | 19329.0000 | 1.0000 | 0.3531 | 0.2451 | 0.3065 | 0.8652 | 0.0098 | 0.0005 | 0.0159 |
| all | 5.0000 | overlapping | 2.0000 | majority_baseline | 19329.0000 | 1.0000 | 0.3333 | 0.2091 | 0.0000 | 0.0000 |  |  | 0.0089 |
| all | 5.0000 | overlapping | 2.0000 | ridge_current | 19329.0000 | 1.0000 | 0.3156 | 0.2268 | 0.3085 | 0.5777 | 0.0071 | 0.0108 | 0.0501 |
| all | 5.0000 | overlapping | 2.0000 | ridge_decay_market | 19329.0000 | 1.0000 | 0.3122 | 0.2234 | 0.2939 | 0.6352 | 0.0090 | 0.0079 | 0.0405 |
| all | 5.0000 | overlapping | 2.0000 | ridge_decay_only | 19329.0000 | 1.0000 | 0.3110 | 0.2219 | 0.3007 | 0.5973 | 0.0078 | 0.0103 | 0.0330 |
| all | 5.0000 | overlapping | 2.0000 | ridge_decay_volume | 19329.0000 | 1.0000 | 0.3125 | 0.2226 | 0.2987 | 0.6435 | 0.0081 | 0.0096 | 0.0440 |
| all | 5.0000 | overlapping | 3.0000 | logistic_decay_market | 19392.0000 | 1.0000 | 0.3873 | 0.3498 | 0.4723 | 0.1327 | -0.0103 | 0.0009 | 0.0017 |
| all | 5.0000 | overlapping | 3.0000 | majority_baseline | 19392.0000 | 1.0000 | 0.3333 | 0.1961 | 0.0000 | 0.0000 |  |  | 0.0002 |
| all | 5.0000 | overlapping | 3.0000 | ridge_current | 19392.0000 | 1.0000 | 0.3624 | 0.2445 | 0.4810 | 0.0051 | -0.0087 | 0.0013 | -0.0017 |
| all | 5.0000 | overlapping | 3.0000 | ridge_decay_market | 19392.0000 | 1.0000 | 0.3616 | 0.2404 | 0.4268 | 0.0093 | -0.0050 | 0.0007 | -0.0009 |
| all | 5.0000 | overlapping | 3.0000 | ridge_decay_only | 19392.0000 | 1.0000 | 0.3601 | 0.2420 | 0.4571 | 0.0043 | -0.0049 | 0.0015 | -0.0021 |
| all | 5.0000 | overlapping | 3.0000 | ridge_decay_volume | 19392.0000 | 1.0000 | 0.3580 | 0.2405 | 0.3984 | 0.0068 | -0.0054 | 0.0011 | -0.0014 |
| all | 5.0000 | overlapping | 4.0000 | logistic_decay_market | 18432.0000 | 1.0000 | 0.3635 | 0.3639 | 0.3945 | 0.4070 | 0.0132 | -0.0005 | 0.0055 |
| all | 5.0000 | overlapping | 4.0000 | majority_baseline | 18432.0000 | 1.0000 | 0.3333 | 0.2014 | 0.0000 | 0.0000 |  |  | 0.0077 |
| all | 5.0000 | overlapping | 4.0000 | ridge_current | 18432.0000 | 1.0000 | 0.3535 | 0.1882 | 0.3830 | 0.0098 | 0.0292 | 0.0068 | 0.0108 |
| all | 5.0000 | overlapping | 4.0000 | ridge_decay_market | 18432.0000 | 1.0000 | 0.3398 | 0.2476 | 0.4086 | 0.1570 | 0.0012 | 0.0093 | 0.0068 |
| all | 5.0000 | overlapping | 4.0000 | ridge_decay_only | 18432.0000 | 1.0000 | 0.3547 | 0.1950 | 0.4325 | 0.0149 | 0.0110 | 0.0073 | 0.0095 |
| all | 5.0000 | overlapping | 4.0000 | ridge_decay_volume | 18432.0000 | 1.0000 | 0.3508 | 0.2009 | 0.4836 | 0.0181 | -0.0012 | 0.0078 | 0.0081 |
| all | 5.0000 | non_overlapping | 0.0000 | logistic_decay_market | 15427.0000 | 1.0000 | 0.3604 | 0.3618 | 0.3642 | 0.4196 | 0.0075 | -0.0004 | 0.0042 |
| all | 5.0000 | non_overlapping | 0.0000 | majority_baseline | 15427.0000 | 1.0000 | 0.3333 | 0.2030 | 0.0000 | 0.0000 |  |  | 0.0049 |
| all | 5.0000 | non_overlapping | 0.0000 | ridge_current | 15427.0000 | 1.0000 | 0.3326 | 0.2843 | 0.3280 | 0.1413 | 0.0065 | 0.0050 | 0.0038 |
| all | 5.0000 | non_overlapping | 0.0000 | ridge_decay_market | 15427.0000 | 1.0000 | 0.3293 | 0.2921 | 0.3244 | 0.1896 | 0.0086 | 0.0044 | 0.0028 |
| all | 5.0000 | non_overlapping | 0.0000 | ridge_decay_only | 15427.0000 | 1.0000 | 0.3290 | 0.2835 | 0.3166 | 0.1479 | 0.0082 | 0.0049 | 0.0029 |
| all | 5.0000 | non_overlapping | 0.0000 | ridge_decay_volume | 15427.0000 | 1.0000 | 0.3234 | 0.2838 | 0.3133 | 0.1580 | 0.0079 | 0.0048 | 0.0032 |
| all | 5.0000 | non_overlapping | 1.0000 | logistic_decay_market | 4074.0000 | 1.0000 | 0.3585 | 0.3490 | 0.4109 | 0.3139 | 0.0027 | -0.0031 | 0.0029 |
| all | 5.0000 | non_overlapping | 1.0000 | majority_baseline | 4074.0000 | 1.0000 | 0.3333 | 0.2045 | 0.0000 | 0.0000 |  |  | 0.0024 |
| all | 5.0000 | non_overlapping | 1.0000 | ridge_current | 4074.0000 | 1.0000 | 0.3426 | 0.2844 | 0.3785 | 0.0435 | 0.0034 | 0.0015 | 0.0027 |
| all | 5.0000 | non_overlapping | 1.0000 | ridge_decay_market | 4074.0000 | 1.0000 | 0.3359 | 0.2882 | 0.3109 | 0.0629 | 0.0151 | -0.0034 | 0.0032 |
| all | 5.0000 | non_overlapping | 1.0000 | ridge_decay_only | 4074.0000 | 1.0000 | 0.3347 | 0.2822 | 0.3305 | 0.0512 | 0.0085 | 0.0002 | 0.0028 |
| all | 5.0000 | non_overlapping | 1.0000 | ridge_decay_volume | 4074.0000 | 1.0000 | 0.3329 | 0.2776 | 0.3160 | 0.0435 | 0.0121 | 0.0005 | 0.0024 |
| all | 5.0000 | non_overlapping | 2.0000 | logistic_decay_market | 3865.0000 | 1.0000 | 0.3594 | 0.2574 | 0.3198 | 0.8679 | 0.0089 | 0.0008 | 0.0147 |
| all | 5.0000 | non_overlapping | 2.0000 | majority_baseline | 3865.0000 | 1.0000 | 0.3333 | 0.2065 | 0.0000 | 0.0000 |  |  | 0.0082 |
| all | 5.0000 | non_overlapping | 2.0000 | ridge_current | 3865.0000 | 1.0000 | 0.3171 | 0.2316 | 0.3186 | 0.5751 | 0.0069 | 0.0090 | 0.0594 |
| all | 5.0000 | non_overlapping | 2.0000 | ridge_decay_market | 3865.0000 | 1.0000 | 0.3338 | 0.2457 | 0.3119 | 0.6169 | 0.0089 | 0.0060 | 0.0370 |
| all | 5.0000 | non_overlapping | 2.0000 | ridge_decay_only | 3865.0000 | 1.0000 | 0.3167 | 0.2284 | 0.3099 | 0.5906 | 0.0083 | 0.0075 | 0.0573 |
| all | 5.0000 | non_overlapping | 2.0000 | ridge_decay_volume | 3865.0000 | 1.0000 | 0.3149 | 0.2272 | 0.3096 | 0.6481 | 0.0075 | 0.0082 | 0.0583 |
| all | 5.0000 | non_overlapping | 3.0000 | logistic_decay_market | 3840.0000 | 1.0000 | 0.3941 | 0.3548 | 0.5103 | 0.1447 | -0.0128 | -0.0006 | 0.0022 |
| all | 5.0000 | non_overlapping | 3.0000 | majority_baseline | 3840.0000 | 1.0000 | 0.3333 | 0.1956 | 0.0000 | 0.0000 |  |  | -0.0002 |
| all | 5.0000 | non_overlapping | 3.0000 | ridge_current | 3840.0000 | 1.0000 | 0.3580 | 0.2342 | 0.5833 | 0.0046 | -0.0113 | 0.0006 | -0.0017 |
| all | 5.0000 | non_overlapping | 3.0000 | ridge_decay_market | 3840.0000 | 1.0000 | 0.3496 | 0.2264 | 0.3750 | 0.0098 | 0.0088 | -0.0004 | -0.0002 |
| all | 5.0000 | non_overlapping | 3.0000 | ridge_decay_only | 3840.0000 | 1.0000 | 0.3557 | 0.2325 | 0.5882 | 0.0065 | -0.0121 | 0.0012 | -0.0030 |
| all | 5.0000 | non_overlapping | 3.0000 | ridge_decay_volume | 3840.0000 | 1.0000 | 0.3492 | 0.2292 | 0.4643 | 0.0085 | 0.0001 | 0.0003 | -0.0014 |
| all | 5.0000 | non_overlapping | 4.0000 | logistic_decay_market | 3648.0000 | 1.0000 | 0.3704 | 0.3700 | 0.3822 | 0.4479 | 0.0136 | 0.0001 | 0.0089 |
| all | 5.0000 | non_overlapping | 4.0000 | majority_baseline | 3648.0000 | 1.0000 | 0.3333 | 0.2053 | 0.0000 | 0.0000 |  |  | 0.0094 |
| all | 5.0000 | non_overlapping | 4.0000 | ridge_current | 3648.0000 | 1.0000 | 0.3547 | 0.1971 | 0.5366 | 0.0164 | 0.0043 | 0.0076 | 0.0209 |
| all | 5.0000 | non_overlapping | 4.0000 | ridge_decay_market | 3648.0000 | 1.0000 | 0.3358 | 0.2415 | 0.3853 | 0.1525 | 0.0034 | 0.0115 | 0.0047 |
| all | 5.0000 | non_overlapping | 4.0000 | ridge_decay_only | 3648.0000 | 1.0000 | 0.3512 | 0.1993 | 0.4545 | 0.0186 | 0.0109 | 0.0084 | 0.0153 |
| all | 5.0000 | non_overlapping | 4.0000 | ridge_decay_volume | 3648.0000 | 1.0000 | 0.3452 | 0.2017 | 0.4038 | 0.0156 | 0.0147 | 0.0088 | 0.0118 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | logistic_decay_market | 8756.0000 | 1.0000 | 0.3213 | 0.3145 | 0.3582 | 0.4774 | 0.0163 | 0.0099 | 0.0081 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | majority_baseline | 8756.0000 | 1.0000 | 0.3308 | 0.3071 | 0.3720 | 0.4921 | 0.0097 |  | 0.0150 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | ridge_current | 8756.0000 | 1.0000 | 0.3246 | 0.3198 | 0.3634 | 0.4152 | 0.0135 | 0.0134 | 0.0103 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | ridge_decay_market | 8756.0000 | 1.0000 | 0.3171 | 0.3133 | 0.3471 | 0.4074 | 0.0181 | 0.0135 | 0.0050 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | ridge_decay_only | 8756.0000 | 1.0000 | 0.3209 | 0.3154 | 0.3534 | 0.4263 | 0.0160 | 0.0127 | 0.0075 |
| semiconductor_ai | 5.0000 | overlapping | 0.0000 | ridge_decay_volume | 8756.0000 | 1.0000 | 0.3179 | 0.3139 | 0.3502 | 0.3972 | 0.0166 | 0.0129 | 0.0072 |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | logistic_decay_market | 2222.0000 | 1.0000 | 0.3319 | 0.3247 | 0.4336 | 0.3183 | 0.0042 | 0.0148 | -0.0028 |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | majority_baseline | 2222.0000 | 1.0000 | 0.3333 | 0.2032 | 0.4383 | 1.0000 | 0.0010 |  |  |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | ridge_current | 2222.0000 | 1.0000 | 0.3570 | 0.3523 | 0.4636 | 0.3532 | 0.0010 | 0.0008 | 0.0012 |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | ridge_decay_market | 2222.0000 | 1.0000 | 0.3365 | 0.3272 | 0.4027 | 0.2762 | 0.0137 | -0.0113 | -0.0032 |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | ridge_decay_only | 2222.0000 | 1.0000 | 0.3322 | 0.3294 | 0.4255 | 0.3460 | 0.0069 | -0.0039 | -0.0018 |
| semiconductor_ai | 5.0000 | overlapping | 1.0000 | ridge_decay_volume | 2222.0000 | 1.0000 | 0.3460 | 0.3381 | 0.4318 | 0.2988 | 0.0083 | -0.0033 | -0.0019 |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | logistic_decay_market | 2200.0000 | 1.0000 | 0.3333 | 0.1586 | 0.3052 | 0.9955 | 0.0184 | 0.0237 | 0.0194 |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | majority_baseline | 2200.0000 | 1.0000 | 0.3333 | 0.1558 | 0.3050 | 1.0000 | 0.0184 |  |  |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | ridge_current | 2200.0000 | 1.0000 | 0.3305 | 0.1664 | 0.3044 | 0.9717 | 0.0182 | 0.0283 | 0.0177 |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | ridge_decay_market | 2200.0000 | 1.0000 | 0.3346 | 0.1785 | 0.3065 | 0.9627 | 0.0183 | 0.0154 | 0.0422 |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | ridge_decay_only | 2200.0000 | 1.0000 | 0.3315 | 0.1658 | 0.3065 | 0.9776 | 0.0182 | 0.0304 | 0.0050 |
| semiconductor_ai | 5.0000 | overlapping | 2.0000 | ridge_decay_volume | 2200.0000 | 1.0000 | 0.3313 | 0.1690 | 0.3071 | 0.9717 | 0.0183 | 0.0199 | 0.0285 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | logistic_decay_market | 2222.0000 | 1.0000 | 0.3704 | 0.3558 | 0.4909 | 0.2628 | -0.0026 | 0.0036 | 0.0088 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | majority_baseline | 2222.0000 | 1.0000 | 0.3333 | 0.2076 | 0.0000 | 0.0000 |  |  | 0.0057 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | ridge_current | 2222.0000 | 1.0000 | 0.3787 | 0.3281 | 0.4979 | 0.1314 | -0.0076 | 0.0003 | 0.0117 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | ridge_decay_market | 2222.0000 | 1.0000 | 0.3682 | 0.3257 | 0.4808 | 0.1498 | -0.0025 | 0.0052 | 0.0079 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | ridge_decay_only | 2222.0000 | 1.0000 | 0.3911 | 0.3398 | 0.4739 | 0.1477 | -0.0019 | -0.0000 | 0.0110 |
| semiconductor_ai | 5.0000 | overlapping | 3.0000 | ridge_decay_volume | 2222.0000 | 1.0000 | 0.3745 | 0.3230 | 0.4432 | 0.1270 | 0.0015 | -0.0008 | 0.0106 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | logistic_decay_market | 2112.0000 | 1.0000 | 0.3202 | 0.3024 | 0.3551 | 0.4839 | 0.0288 | 0.0597 | 0.0205 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | majority_baseline | 2112.0000 | 1.0000 | 0.3333 | 0.2338 | 0.0000 | 0.0000 |  |  | 0.0248 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | ridge_current | 2112.0000 | 1.0000 | 0.3200 | 0.3055 | 0.3911 | 0.3488 | 0.0200 | 0.0359 | 0.0208 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | ridge_decay_market | 2112.0000 | 1.0000 | 0.3192 | 0.3018 | 0.3589 | 0.3977 | 0.0278 | 0.0365 | 0.0140 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | ridge_decay_only | 2112.0000 | 1.0000 | 0.3134 | 0.2989 | 0.3641 | 0.3810 | 0.0254 | 0.0385 | 0.0155 |
| semiconductor_ai | 5.0000 | overlapping | 4.0000 | ridge_decay_volume | 2112.0000 | 1.0000 | 0.3126 | 0.2951 | 0.3666 | 0.3449 | 0.0247 | 0.0370 | 0.0161 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | logistic_decay_market | 1760.0000 | 1.0000 | 0.3278 | 0.3163 | 0.3608 | 0.5100 | 0.0142 | 0.0111 | 0.0145 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | majority_baseline | 1760.0000 | 1.0000 | 0.3309 | 0.3051 | 0.3625 | 0.5008 | 0.0120 |  | 0.0164 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | ridge_current | 1760.0000 | 1.0000 | 0.3235 | 0.3194 | 0.3584 | 0.4165 | 0.0139 | 0.0108 | 0.0165 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | ridge_decay_market | 1760.0000 | 1.0000 | 0.3188 | 0.3135 | 0.3427 | 0.4303 | 0.0183 | 0.0122 | 0.0097 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | ridge_decay_only | 1760.0000 | 1.0000 | 0.3090 | 0.3042 | 0.3460 | 0.4334 | 0.0155 | 0.0103 | 0.0146 |
| semiconductor_ai | 5.0000 | non_overlapping | 0.0000 | ridge_decay_volume | 1760.0000 | 1.0000 | 0.3080 | 0.3054 | 0.3441 | 0.4074 | 0.0162 | 0.0099 | 0.0141 |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | logistic_decay_market | 462.0000 | 1.0000 | 0.3241 | 0.3209 | 0.4303 | 0.3679 | -0.0020 | 0.0321 | 0.0050 |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | majority_baseline | 462.0000 | 1.0000 | 0.3333 | 0.1964 | 0.4177 | 1.0000 | 0.0052 |  |  |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | ridge_current | 462.0000 | 1.0000 | 0.3615 | 0.3569 | 0.4521 | 0.3420 | 0.0018 | -0.0088 | 0.0140 |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | ridge_decay_market | 462.0000 | 1.0000 | 0.3434 | 0.3411 | 0.4085 | 0.3472 | 0.0072 | -0.0197 | 0.0102 |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | ridge_decay_only | 462.0000 | 1.0000 | 0.3119 | 0.3158 | 0.4251 | 0.3679 | -0.0002 | -0.0002 | 0.0121 |
| semiconductor_ai | 5.0000 | non_overlapping | 1.0000 | ridge_decay_volume | 462.0000 | 1.0000 | 0.3340 | 0.3323 | 0.4324 | 0.3316 | -0.0016 | 0.0017 | 0.0106 |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | logistic_decay_market | 440.0000 | 1.0000 | 0.3366 | 0.1675 | 0.3065 | 0.9925 | 0.0190 | 0.0197 | 0.0291 |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | majority_baseline | 440.0000 | 1.0000 | 0.3333 | 0.1556 | 0.3045 | 1.0000 | 0.0192 |  |  |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | ridge_current | 440.0000 | 1.0000 | 0.3474 | 0.1902 | 0.3113 | 0.9851 | 0.0186 | 0.0375 | 0.0107 |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | ridge_decay_market | 440.0000 | 1.0000 | 0.3374 | 0.1874 | 0.3062 | 0.9552 | 0.0194 | 0.0082 | 0.0349 |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | ridge_decay_only | 440.0000 | 1.0000 | 0.3361 | 0.1741 | 0.3082 | 0.9776 | 0.0187 | 0.0341 | 0.0107 |
| semiconductor_ai | 5.0000 | non_overlapping | 2.0000 | ridge_decay_volume | 440.0000 | 1.0000 | 0.3332 | 0.1701 | 0.3104 | 0.9776 | 0.0188 | 0.0249 | 0.0414 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | logistic_decay_market | 440.0000 | 1.0000 | 0.3984 | 0.3819 | 0.5588 | 0.3032 | -0.0079 | -0.0167 | 0.0107 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | majority_baseline | 440.0000 | 1.0000 | 0.3333 | 0.2047 | 0.0000 | 0.0000 |  |  | 0.0039 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | ridge_current | 440.0000 | 1.0000 | 0.3767 | 0.3394 | 0.5818 | 0.1702 | -0.0192 | 0.0031 | 0.0092 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | ridge_decay_market | 440.0000 | 1.0000 | 0.3572 | 0.3125 | 0.4815 | 0.1383 | -0.0032 | 0.0079 | 0.0032 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | ridge_decay_only | 440.0000 | 1.0000 | 0.3775 | 0.3268 | 0.5000 | 0.1383 | -0.0029 | -0.0018 | 0.0084 |
| semiconductor_ai | 5.0000 | non_overlapping | 3.0000 | ridge_decay_volume | 440.0000 | 1.0000 | 0.3610 | 0.3066 | 0.4444 | 0.1064 | 0.0038 | -0.0041 | 0.0088 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | logistic_decay_market | 418.0000 | 1.0000 | 0.3233 | 0.2961 | 0.3243 | 0.5217 | 0.0270 | 0.0937 | 0.0321 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | majority_baseline | 418.0000 | 1.0000 | 0.3333 | 0.2379 | 0.0000 | 0.0000 |  |  | 0.0297 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | ridge_current | 418.0000 | 1.0000 | 0.2899 | 0.2790 | 0.3134 | 0.3043 | 0.0258 | 0.0306 | 0.0323 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | ridge_decay_market | 418.0000 | 1.0000 | 0.3260 | 0.3022 | 0.3261 | 0.4348 | 0.0321 | 0.0387 | 0.0198 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | ridge_decay_only | 418.0000 | 1.0000 | 0.2933 | 0.2827 | 0.3161 | 0.3986 | 0.0279 | 0.0343 | 0.0287 |
| semiconductor_ai | 5.0000 | non_overlapping | 4.0000 | ridge_decay_volume | 418.0000 | 1.0000 | 0.2920 | 0.2820 | 0.3228 | 0.3696 | 0.0295 | 0.0326 | 0.0278 |

## MU and NBIS diagnostics

| scope | ticker | observation_date | horizon | fold | specification | actual_return | actual_direction | predicted_direction | training_samples | predicted_return |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | ridge_current | -0.1436 | down | neutral | 76666.0000 | 0.0042 |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | ridge_current | -0.0477 | down | up | 76666.0000 | 0.0231 |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | ridge_decay_only | -0.1436 | down | neutral | 76666.0000 | 0.0059 |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_only | -0.0477 | down | up | 76666.0000 | 0.0337 |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | ridge_decay_volume | -0.1436 | down | neutral | 76666.0000 | 0.0095 |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_volume | -0.0477 | down | up | 76666.0000 | 0.0332 |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | ridge_decay_market | -0.1436 | down | neutral | 76666.0000 | 0.0088 |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_market | -0.0477 | down | up | 76666.0000 | 0.0346 |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | majority_baseline | -0.1436 | down | up | 76666.0000 |  |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | majority_baseline | -0.0477 | down | up | 76666.0000 |  |
| all | MU | 2026-06-25 | 5.0000 | 4.0000 | logistic_decay_market | -0.1436 | down | up | 76666.0000 |  |
| all | MU | 2026-07-01 | 5.0000 | 4.0000 | logistic_decay_market | -0.0477 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | ridge_current | -0.0721 | down | up | 76666.0000 | 0.0298 |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_only | -0.0721 | down | up | 76666.0000 | 0.0424 |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_volume | -0.0721 | down | up | 76666.0000 | 0.0438 |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | ridge_decay_market | -0.0721 | down | up | 76666.0000 | 0.0489 |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | majority_baseline | -0.0721 | down | up | 76666.0000 |  |
| all | NBIS | 2026-07-01 | 5.0000 | 4.0000 | logistic_decay_market | -0.0721 | down | up | 76666.0000 |  |

## Interpretation

The decay-only, volume-confirmed, and market-confirmed specifications use identical executable observations and folds. A candidate must improve aggregate balanced accuracy and macro F1, preserve downside recall, retain the improvement on non-overlapping outcomes, win a majority of eligible folds, and avoid material semiconductor degradation. Predicted-down returns must be negative and ordered below predicted-up returns. Named event dates are diagnostics only and a candidate must correct at least one known false-bull case without adding another.
