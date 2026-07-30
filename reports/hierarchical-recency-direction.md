# 时间衰减与层级方向挑战模型

- 指标门槛：未通过
- 线上权限：无；本实验不修改线上 Ridge。
- 限制：股票池与分类尚不具备完整 point-in-time 历史。
- 门槛原因：overlapping:balanced_accuracy_gain_below_one_point, overlapping:macro_f1_gain_below_one_point, overlapping:return_ordering_failed, non_overlapping:balanced_accuracy_gain_below_one_point, non_overlapping:macro_f1_gain_below_one_point, non_overlapping:return_ordering_failed, non_overlapping:fold_wins_below_four, under_pressure:down_recall_not_improved

## 全体样本指标

| horizon | sample_mode | specification | sample_count | balanced_accuracy | macro_f1 | down_recall | mean_return_predicted_down | mean_return_predicted_neutral | mean_return_predicted_up |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | overlapping | logistic_global | 384686 | 0.4008 | 0.3548 | 0.3038 | 0.0092 | 0.0023 | 0.0047 |
| 5 | overlapping | logistic_group | 384686 | 0.4002 | 0.3620 | 0.3420 | 0.0084 | 0.0023 | 0.0047 |
| 5 | overlapping | logistic_time | 384686 | 0.4000 | 0.3517 | 0.3037 | 0.0086 | 0.0022 | 0.0055 |
| 5 | overlapping | logistic_time_group | 384686 | 0.3998 | 0.3580 | 0.3311 | 0.0082 | 0.0023 | 0.0053 |
| 5 | overlapping | logistic_time_group_ticker | 384686 | 0.4045 | 0.3585 | 0.3398 | 0.0081 | 0.0023 | 0.0055 |
| 5 | overlapping | majority_baseline | 384686 | 0.3333 | 0.2027 | 0.0000 | — | — | 0.0050 |
| 5 | overlapping | ridge_current | 384686 | 0.3535 | 0.3268 | 0.2437 | 0.0037 | 0.0031 | 0.0080 |
| 5 | non_overlapping | logistic_global | 77175 | 0.4056 | 0.3601 | 0.3088 | 0.0088 | 0.0023 | 0.0068 |
| 5 | non_overlapping | logistic_group | 77175 | 0.4035 | 0.3657 | 0.3455 | 0.0083 | 0.0023 | 0.0066 |
| 5 | non_overlapping | logistic_time | 77175 | 0.4008 | 0.3530 | 0.3065 | 0.0088 | 0.0024 | 0.0069 |
| 5 | non_overlapping | logistic_time_group | 77175 | 0.4014 | 0.3598 | 0.3346 | 0.0085 | 0.0023 | 0.0065 |
| 5 | non_overlapping | logistic_time_group_ticker | 77175 | 0.4066 | 0.3606 | 0.3408 | 0.0086 | 0.0022 | 0.0068 |
| 5 | non_overlapping | majority_baseline | 77175 | 0.3333 | 0.2035 | 0.0000 | — | — | 0.0055 |
| 5 | non_overlapping | ridge_current | 77175 | 0.3550 | 0.3293 | 0.2515 | 0.0035 | 0.0033 | 0.0094 |
| 20 | overlapping | logistic_global | 381086 | 0.3865 | 0.3469 | 0.3190 | 0.0378 | 0.0089 | 0.0252 |
| 20 | overlapping | logistic_group | 381086 | 0.3879 | 0.3536 | 0.3418 | 0.0369 | 0.0083 | 0.0245 |
| 20 | overlapping | logistic_time | 381086 | 0.3886 | 0.3431 | 0.3186 | 0.0381 | 0.0094 | 0.0258 |
| 20 | overlapping | logistic_time_group | 381086 | 0.3906 | 0.3507 | 0.3426 | 0.0370 | 0.0090 | 0.0250 |
| 20 | overlapping | logistic_time_group_ticker | 381086 | 0.3962 | 0.3523 | 0.3523 | 0.0376 | 0.0093 | 0.0243 |
| 20 | overlapping | majority_baseline | 381086 | 0.3333 | 0.2123 | 0.0000 | — | — | 0.0230 |
| 20 | overlapping | ridge_current | 381086 | 0.3459 | 0.3297 | 0.2351 | 0.0170 | 0.0147 | 0.0320 |
| 20 | non_overlapping | logistic_global | 19137 | 0.3926 | 0.3523 | 0.3079 | 0.0369 | 0.0094 | 0.0270 |
| 20 | non_overlapping | logistic_group | 19137 | 0.3923 | 0.3585 | 0.3281 | 0.0362 | 0.0084 | 0.0264 |
| 20 | non_overlapping | logistic_time | 19137 | 0.3977 | 0.3528 | 0.3177 | 0.0346 | 0.0110 | 0.0285 |
| 20 | non_overlapping | logistic_time_group | 19137 | 0.3979 | 0.3584 | 0.3335 | 0.0358 | 0.0098 | 0.0265 |
| 20 | non_overlapping | logistic_time_group_ticker | 19137 | 0.4012 | 0.3587 | 0.3446 | 0.0363 | 0.0100 | 0.0260 |
| 20 | non_overlapping | majority_baseline | 19137 | 0.3333 | 0.2117 | 0.0000 | — | — | 0.0232 |
| 20 | non_overlapping | ridge_current | 19137 | 0.3432 | 0.3291 | 0.2361 | 0.0196 | 0.0140 | 0.0317 |
| 60 | overlapping | logistic_global | 371486 | 0.3854 | 0.3640 | 0.3608 | 0.1000 | 0.0285 | 0.0959 |
| 60 | overlapping | logistic_group | 371486 | 0.3866 | 0.3667 | 0.3615 | 0.1039 | 0.0258 | 0.0922 |
| 60 | overlapping | logistic_time | 371486 | 0.3792 | 0.3536 | 0.3344 | 0.1156 | 0.0293 | 0.0850 |
| 60 | overlapping | logistic_time_group | 371486 | 0.3810 | 0.3582 | 0.3438 | 0.1171 | 0.0269 | 0.0817 |
| 60 | overlapping | logistic_time_group_ticker | 371486 | 0.3910 | 0.3622 | 0.3554 | 0.1273 | 0.0269 | 0.0750 |
| 60 | overlapping | majority_baseline | 371486 | 0.3333 | 0.2171 | 0.0000 | — | — | 0.0765 |
| 60 | overlapping | ridge_current | 371486 | 0.3447 | 0.3331 | 0.2088 | 0.0531 | 0.0366 | 0.1076 |
| 60 | non_overlapping | logistic_global | 6239 | 0.3547 | 0.3339 | 0.3266 | 0.1113 | 0.0276 | 0.0633 |
| 60 | non_overlapping | logistic_group | 6239 | 0.3688 | 0.3495 | 0.3341 | 0.1151 | 0.0249 | 0.0611 |
| 60 | non_overlapping | logistic_time | 6239 | 0.3553 | 0.3292 | 0.3126 | 0.1224 | 0.0271 | 0.0570 |
| 60 | non_overlapping | logistic_time_group | 6239 | 0.3641 | 0.3412 | 0.3168 | 0.1261 | 0.0234 | 0.0553 |
| 60 | non_overlapping | logistic_time_group_ticker | 6239 | 0.3869 | 0.3574 | 0.3472 | 0.1217 | 0.0273 | 0.0586 |
| 60 | non_overlapping | majority_baseline | 6239 | 0.3333 | 0.2162 | 0.0000 | — | — | 0.0693 |
| 60 | non_overlapping | ridge_current | 6239 | 0.3224 | 0.3123 | 0.1953 | 0.0518 | 0.0421 | 0.0928 |

## 五日市场阶段指标

| regime | sample_mode | specification | sample_count | balanced_accuracy | macro_f1 | down_recall |
| --- | --- | --- | --- | --- | --- | --- |
| acute_selloff | overlapping | logistic_global | 10901 | 0.3637 | 0.3586 | 0.1951 |
| acute_selloff | overlapping | logistic_time_group_ticker | 10901 | 0.4115 | 0.3970 | 0.3456 |
| correction | overlapping | logistic_global | 58610 | 0.3861 | 0.3687 | 0.2327 |
| correction | overlapping | logistic_time_group_ticker | 58610 | 0.3972 | 0.3651 | 0.2762 |
| range_bound | overlapping | logistic_global | 35312 | 0.3949 | 0.3488 | 0.3512 |
| range_bound | overlapping | logistic_time_group_ticker | 35312 | 0.4082 | 0.3534 | 0.4277 |
| under_pressure | overlapping | logistic_global | 85842 | 0.3819 | 0.3463 | 0.2566 |
| under_pressure | overlapping | logistic_time_group_ticker | 85842 | 0.3905 | 0.3470 | 0.2410 |
| uptrend | overlapping | logistic_global | 194021 | 0.3983 | 0.3236 | 0.3437 |
| uptrend | overlapping | logistic_time_group_ticker | 194021 | 0.4030 | 0.3428 | 0.3888 |
| acute_selloff | non_overlapping | logistic_global | 2459 | 0.3980 | 0.3980 | 0.2650 |
| acute_selloff | non_overlapping | logistic_time_group_ticker | 2459 | 0.4173 | 0.4099 | 0.4040 |
| correction | non_overlapping | logistic_global | 11248 | 0.3868 | 0.3699 | 0.2599 |
| correction | non_overlapping | logistic_time_group_ticker | 11248 | 0.3917 | 0.3573 | 0.2949 |
| range_bound | non_overlapping | logistic_global | 7085 | 0.3903 | 0.3501 | 0.3347 |
| range_bound | non_overlapping | logistic_time_group_ticker | 7085 | 0.4029 | 0.3531 | 0.3931 |
| under_pressure | non_overlapping | logistic_global | 18799 | 0.3813 | 0.3439 | 0.2536 |
| under_pressure | non_overlapping | logistic_time_group_ticker | 18799 | 0.3944 | 0.3504 | 0.2501 |
| uptrend | non_overlapping | logistic_global | 37584 | 0.3995 | 0.3275 | 0.3478 |
| uptrend | non_overlapping | logistic_time_group_ticker | 37584 | 0.4019 | 0.3435 | 0.3866 |

## 核心结论

- 指标门槛未通过时，挑战模型不得进入 UI、最终方向或否决策略。
- 若“预测下跌”组平均实际收益不为负，说明方向排序仍未形成可交易语义。
