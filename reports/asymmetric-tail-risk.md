# 不对称五日尾部风险研究

> 离线研究模型；`online_authority=none`。不修改 Ridge、最终方向、否决策略或 UI。

## 结论

- 研究门槛：`rejected`
- 是否通过：`False`
- 所有经济收益均使用未截尾、未 Winsorize、未删除暴涨样本的真实五日终点收益。

## 非重叠总体结果

| row_count | risk_count | coverage | down_precision | baseline_down_precision | down_precision_gain | mean_terminal_return | risk_rebound_rate | all_rebound_rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | — | — | — | — | — | — | — |

## 训练内候选边界

| fold | down_threshold | rebound_cap | risk_count | coverage | down_precision | mean_terminal_return | reasons |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.4000 | 0.2000 | 547 | 0.0117 | 0.4936 | 0.0231 | insufficient_risk_coverage, non_negative_risk_return |
| 1 | 0.4000 | 0.3000 | 815 | 0.0174 | 0.5337 | 0.0406 | insufficient_risk_coverage, non_negative_risk_return |
| 1 | 0.5000 | 0.2000 | 213 | 0.0046 | 0.6197 | -0.0023 | insufficient_risk_rows, insufficient_risk_coverage |
| 1 | 0.5000 | 0.3000 | 477 | 0.0102 | 0.6164 | 0.0423 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 1 | 0.6000 | 0.2000 | 161 | 0.0034 | 0.6584 | -0.0066 | insufficient_risk_rows, insufficient_risk_coverage |
| 1 | 0.6000 | 0.3000 | 412 | 0.0088 | 0.6238 | 0.0520 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.4000 | 0.2000 | 867 | 0.0089 | 0.4325 | 0.0201 | insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.4000 | 0.3000 | 1354 | 0.0139 | 0.4705 | 0.0201 | insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.5000 | 0.2000 | 39 | 0.0004 | 0.4359 | 0.1445 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.5000 | 0.3000 | 155 | 0.0016 | 0.5548 | 0.0245 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.6000 | 0.2000 | 8 | 0.0001 | 0.2500 | 0.2151 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 2 | 0.6000 | 0.3000 | 25 | 0.0003 | 0.4800 | 0.0989 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 3 | 0.4000 | 0.2000 | 2006 | 0.0129 | 0.4482 | 0.0132 | insufficient_risk_coverage, non_negative_risk_return |
| 3 | 0.4000 | 0.3000 | 2568 | 0.0165 | 0.4755 | 0.0109 | insufficient_risk_coverage, non_negative_risk_return |
| 3 | 0.5000 | 0.2000 | 9 | 0.0001 | 0.4444 | 0.0021 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 3 | 0.5000 | 0.3000 | 180 | 0.0012 | 0.6444 | -0.0161 | insufficient_risk_rows, insufficient_risk_coverage |
| 3 | 0.6000 | 0.2000 | 0 | 0.0000 | — | — | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 3 | 0.6000 | 0.3000 | 26 | 0.0002 | 0.7308 | -0.0174 | insufficient_risk_rows, insufficient_risk_coverage |
| 4 | 0.4000 | 0.2000 | 3282 | 0.0150 | 0.4555 | 0.0111 | insufficient_risk_coverage, non_negative_risk_return |
| 4 | 0.4000 | 0.3000 | 4557 | 0.0208 | 0.4900 | 0.0105 | insufficient_risk_coverage, non_negative_risk_return |
| 4 | 0.5000 | 0.2000 | 131 | 0.0006 | 0.5573 | -0.0096 | insufficient_risk_rows, insufficient_risk_coverage |
| 4 | 0.5000 | 0.3000 | 1014 | 0.0046 | 0.5947 | 0.0063 | insufficient_risk_coverage, non_negative_risk_return |
| 4 | 0.6000 | 0.2000 | 0 | 0.0000 | — | — | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 4 | 0.6000 | 0.3000 | 7 | 0.0000 | 0.5714 | -0.2095 | insufficient_risk_rows, insufficient_risk_coverage |
| 5 | 0.4000 | 0.2000 | 4410 | 0.0155 | 0.4744 | 0.0146 | insufficient_risk_coverage, non_negative_risk_return |
| 5 | 0.4000 | 0.3000 | 7111 | 0.0251 | 0.5247 | 0.0162 | insufficient_risk_coverage, non_negative_risk_return |
| 5 | 0.5000 | 0.2000 | 627 | 0.0022 | 0.5550 | 0.0197 | insufficient_risk_coverage, non_negative_risk_return |
| 5 | 0.5000 | 0.3000 | 2890 | 0.0102 | 0.6038 | 0.0213 | insufficient_risk_coverage, non_negative_risk_return |
| 5 | 0.6000 | 0.2000 | 0 | 0.0000 | — | — | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |
| 5 | 0.6000 | 0.3000 | 423 | 0.0015 | 0.6998 | 0.0307 | insufficient_risk_rows, insufficient_risk_coverage, non_negative_risk_return |

## 未通过原因

- `all_outer_boundaries_unavailable`
- `coverage_gate_failed`
- `economic_return_gate_failed`
- `precision_gain_gate_failed`
- `rebound_rate_gate_failed`
- `fold_stability_gate_failed`
- `semiconductor_group_unavailable`
- `software_group_unavailable`

## 方法边界

- 入场为观察日后下一交易日开盘，退出为第五个未来交易日收盘。
- 下跌事件、收益中位数、20% 下分位数和极端反弹概率由四个独立模型头估计。
- 概率校准和组合边界只使用外层训练集内部净化 OOF 结果。
- 极端反弹作为反证保留，不得为了改善均值而删除。
