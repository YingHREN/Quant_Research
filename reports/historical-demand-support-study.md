# 历史需求支撑区样本外消融

- 数据截止：2026-07-24
- 股票数量：235
- 模型权限：`advisory_only`
- 晋级：未通过研究门槛
- 执行定义：观察日收盘形成证据，下一交易日开盘进入观察。
- 主门槛：10 日、重叠样本、相同股票/日期/周期严格配对。
- 完整分层指标见同名 CSV；Markdown 只保留决策摘要。
- 规则分数不是上涨概率；即使研究门槛通过也需人工复核。

## 晋级失败原因

- `stable_fold_wins_below_three`
- `improved_group_count_below_two`
- `ablation_increment_not_positive`
- `causal_audit_failed`

## 10 日主门槛（逐折、逐组）

| variant | fold | group | sample_count | support_hold_rate | support_break_rate | maximum_favorable_excursion | maximum_adverse_excursion |
|---|---|---|---|---|---|---|---|
| baseline | 1 | other | 25987 | 0.4470 | 0.5530 | 0.0452 | -0.0455 |
| baseline | 1 | semiconductor | 12286 | 0.4081 | 0.5919 | 0.0645 | -0.0593 |
| baseline | 1 | software | 18780 | 0.4678 | 0.5322 | 0.0666 | -0.0539 |
| baseline | 2 | other | 23528 | 0.4694 | 0.5306 | 0.0661 | -0.0532 |
| baseline | 2 | semiconductor | 12110 | 0.4607 | 0.5393 | 0.0799 | -0.0590 |
| baseline | 2 | software | 20263 | 0.4655 | 0.5345 | 0.0759 | -0.0576 |
| baseline | 3 | other | 25167 | 0.3954 | 0.6046 | 0.0559 | -0.0557 |
| baseline | 3 | semiconductor | 12821 | 0.3464 | 0.6536 | 0.0730 | -0.0693 |
| baseline | 3 | software | 23764 | 0.3639 | 0.6361 | 0.0725 | -0.0727 |
| baseline | 4 | other | 27885 | 0.4411 | 0.5589 | 0.0532 | -0.0446 |
| baseline | 4 | semiconductor | 13815 | 0.3897 | 0.6103 | 0.0742 | -0.0653 |
| baseline | 4 | software | 27740 | 0.4393 | 0.5607 | 0.0664 | -0.0521 |
| baseline | 5 | other | 26744 | 0.4296 | 0.5704 | 0.0610 | -0.0518 |
| baseline | 5 | semiconductor | 12100 | 0.4021 | 0.5979 | 0.1138 | -0.0766 |
| baseline | 5 | software | 25957 | 0.3807 | 0.6193 | 0.0758 | -0.0681 |
| baseline_plus_historical_demand | 1 | other | 25987 | 0.4283 | 0.5717 | 0.0452 | -0.0455 |
| baseline_plus_historical_demand | 1 | semiconductor | 12286 | 0.3908 | 0.6092 | 0.0645 | -0.0593 |
| baseline_plus_historical_demand | 1 | software | 18780 | 0.4539 | 0.5461 | 0.0666 | -0.0539 |
| baseline_plus_historical_demand | 2 | other | 23528 | 0.4523 | 0.5477 | 0.0661 | -0.0532 |
| baseline_plus_historical_demand | 2 | semiconductor | 12110 | 0.4472 | 0.5528 | 0.0799 | -0.0590 |
| baseline_plus_historical_demand | 2 | software | 20263 | 0.4563 | 0.5437 | 0.0759 | -0.0576 |
| baseline_plus_historical_demand | 3 | other | 25167 | 0.3819 | 0.6181 | 0.0559 | -0.0557 |
| baseline_plus_historical_demand | 3 | semiconductor | 12821 | 0.3314 | 0.6686 | 0.0730 | -0.0693 |
| baseline_plus_historical_demand | 3 | software | 23764 | 0.3538 | 0.6462 | 0.0725 | -0.0727 |
| baseline_plus_historical_demand | 4 | other | 27885 | 0.4233 | 0.5767 | 0.0532 | -0.0446 |
| baseline_plus_historical_demand | 4 | semiconductor | 13815 | 0.3779 | 0.6221 | 0.0742 | -0.0653 |
| baseline_plus_historical_demand | 4 | software | 27740 | 0.4262 | 0.5738 | 0.0664 | -0.0521 |
| baseline_plus_historical_demand | 5 | other | 26744 | 0.4070 | 0.5930 | 0.0610 | -0.0518 |
| baseline_plus_historical_demand | 5 | semiconductor | 12100 | 0.3916 | 0.6084 | 0.1138 | -0.0766 |
| baseline_plus_historical_demand | 5 | software | 25957 | 0.3701 | 0.6299 | 0.0758 | -0.0681 |

## 5/10/20 日稳健性

| horizon | variant | sample_count | support_hold_rate | support_break_rate | maximum_favorable_excursion | maximum_adverse_excursion | final_return |
|---|---|---|---|---|---|---|---|
| 5 | baseline | 309713 | 0.5457 | 0.4543 | 0.0442 | -0.0402 | 0.0037 |
| 5 | baseline_plus_historical_demand | 309713 | 0.5272 | 0.4728 | 0.0442 | -0.0402 | 0.0037 |
| 10 | baseline | 308947 | 0.4228 | 0.5772 | 0.0669 | -0.0574 | 0.0081 |
| 10 | baseline_plus_historical_demand | 308947 | 0.4082 | 0.5918 | 0.0669 | -0.0574 | 0.0081 |
| 20 | baseline | 307575 | 0.3172 | 0.6828 | 0.1024 | -0.0808 | 0.0169 |
| 20 | baseline_plus_historical_demand | 307575 | 0.3054 | 0.6946 | 0.1024 | -0.0808 | 0.0169 |

## 10 日全部合格样本与消融

| variant | sample_count | support_hold_rate | support_break_rate | maximum_favorable_excursion | maximum_adverse_excursion | final_return |
|---|---|---|---|---|---|---|
| baseline | 308947 | 0.4228 | 0.5772 | 0.0669 | -0.0574 | 0.0081 |
| baseline_plus_historical_demand | 357402 | 0.4074 | 0.5926 | 0.0727 | -0.0609 | 0.0093 |
| historical_demand_only | 181842 | 0.4133 | 0.5867 | 0.0778 | -0.0639 | 0.0102 |
| no_decay | 195307 | 0.4107 | 0.5893 | 0.0777 | -0.0639 | 0.0102 |
| no_environment | 164394 | 0.4208 | 0.5792 | 0.0781 | -0.0639 | 0.0103 |
| no_retests | 156686 | 0.4185 | 0.5815 | 0.0780 | -0.0639 | 0.0102 |

## 未纳入股票

- INFQ：fewer_than_220_sessions（137 个交易日）
- MDLN：fewer_than_220_sessions（150 个交易日）
- NAVN：fewer_than_220_sessions（183 个交易日）
- Q：fewer_than_220_sessions（186 个交易日）
- WOLF：fewer_than_220_sessions（206 个交易日）
