# 市场阶段与经济阈值方向挑战模型

- 历史指标门槛：未通过
- 线上权限：无；不修改 Ridge、风险否决、API 或 UI。
- 经济阈值可用折：0/5。
- 未通过原因：overlapping:missing_primary_metrics, non_overlapping:missing_primary_metrics, semiconductor:missing_subgroup_metrics, software:missing_subgroup_metrics, other:missing_subgroup_metrics, under_pressure:missing_metrics, correction:missing_metrics, acute_selloff:missing_metrics, stressed_combined:missing_metrics, ablation_has_no_positive_increment

## 绝对方向

| sample_mode | specification | sample_count | balanced_accuracy | macro_f1 | down_precision | down_recall | down_coverage | mean_return_predicted_down |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| overlapping | logistic_global | 384686 | 0.4008 | 0.3548 | 0.4178 | 0.3038 | 0.2821 | 0.0092 |
| overlapping | logistic_regime_prior | 384686 | 0.4006 | 0.3560 | 0.4229 | 0.2947 | 0.2704 | 0.0084 |
| overlapping | majority_baseline | 384686 | 0.3333 | 0.2027 | 0.0000 | 0.0000 | 0.0000 | — |
| overlapping | ridge_current | 384686 | 0.3535 | 0.3268 | 0.3760 | 0.2437 | 0.2514 | 0.0037 |
| non_overlapping | logistic_global | 77175 | 0.4056 | 0.3601 | 0.4216 | 0.3088 | 0.2826 | 0.0088 |
| non_overlapping | logistic_regime_prior | 77175 | 0.4059 | 0.3613 | 0.4229 | 0.3004 | 0.2741 | 0.0087 |
| non_overlapping | majority_baseline | 77175 | 0.3333 | 0.2035 | 0.0000 | 0.0000 | 0.0000 | — |
| non_overlapping | ridge_current | 77175 | 0.3550 | 0.3293 | 0.3777 | 0.2515 | 0.2569 | 0.0035 |

## QQQ 相对方向（独立诊断）

| horizon | sample_mode | specification | sample_count | balanced_accuracy | macro_f1 | down_coverage | down_mean_absolute_return | down_mean_relative_return | neutral_coverage | neutral_mean_absolute_return | neutral_mean_relative_return | up_coverage | up_mean_absolute_return | up_mean_relative_return |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 5 | overlapping | logistic_qqq_relative | 384686 | 0.3954 | 0.3518 | 0.2737 | 0.0064 | 0.0031 | 0.4634 | 0.0023 | -0.0007 | 0.2628 | 0.0081 | 0.0030 |
| 5 | non_overlapping | logistic_qqq_relative | 77175 | 0.3981 | 0.3555 | 0.2698 | 0.0045 | 0.0020 | 0.4645 | 0.0023 | -0.0008 | 0.2657 | 0.0119 | 0.0054 |

## 解释边界

- 绝对下跌表示股票自身五日可执行收益低于 -1%。
- 相对下跌只表示跑输 QQQ，不得改写为股票绝对下跌。
- 经济阈值只由每个外层训练集内部的净化 OOF 预测选择。
