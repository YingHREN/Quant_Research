# 支撑区首触反应研究

- 数据截止：2026-07-24
- 首触事件：1525199
- 实际触达：1241380
- 模型权限：`advisory_only`
- 标签：首次触达后 3 个交易日内，失败优先于接受；未触达不进入反应率分母。
- 该研究不修改 Ridge、下行否决、最终决策策略或 UI。

## 研究门控

- `performance_condition_failed:acceptance_gain_at_least_2pp`
- `performance_condition_failed:at_least_2_group_wins`
- `causal_audit_failed`
- `future_holdout_required`

## 预注册性能条件（确认队列、10 日严格配对）

- 承接率增量：+0.51 pp
- 失效率变化：-1.24 pp
- 最大穿透 ATR 变化：-0.0237
- 改善时间折：5/5
- 改善板块组：1/1（要求覆盖 3 组且至少 2 组改善）
- 同向距离分箱：4/4
- 条件通过数：4/6

## 10 日严格配对（逐折）

| cohort | variant | fold | event_count | touch_rate | accepted_rate | failed_rate | ambiguous_rate | mean_maximum_rebound_atr | mean_maximum_penetration_atr |
|---|---|---|---|---|---|---|---|---|---|
| confirmation | baseline | 1.0000 | 6681 | 0.7728 | 0.5847 | 0.3728 | 0.0424 | 1.0178 | 0.7492 |
| confirmation | baseline | 2.0000 | 6484 | 0.7725 | 0.5947 | 0.3604 | 0.0449 | 1.0053 | 0.7564 |
| confirmation | baseline | 3.0000 | 7758 | 0.8032 | 0.5720 | 0.3877 | 0.0403 | 0.9734 | 0.7324 |
| confirmation | baseline | 4.0000 | 7510 | 0.7694 | 0.5760 | 0.3809 | 0.0431 | 1.0591 | 0.7424 |
| confirmation | baseline | 5.0000 | 7413 | 0.7825 | 0.5796 | 0.3689 | 0.0515 | 1.0143 | 0.7090 |
| confirmation | baseline_plus_historical_demand | 1.0000 | 6681 | 0.7945 | 0.5908 | 0.3578 | 0.0514 | 0.9985 | 0.7209 |
| confirmation | baseline_plus_historical_demand | 2.0000 | 6484 | 0.7890 | 0.5968 | 0.3477 | 0.0555 | 0.9919 | 0.7346 |
| confirmation | baseline_plus_historical_demand | 3.0000 | 7758 | 0.8202 | 0.5758 | 0.3777 | 0.0465 | 0.9579 | 0.7151 |
| confirmation | baseline_plus_historical_demand | 4.0000 | 7510 | 0.7923 | 0.5854 | 0.3649 | 0.0497 | 1.0471 | 0.7154 |
| confirmation | baseline_plus_historical_demand | 5.0000 | 7413 | 0.8010 | 0.5834 | 0.3602 | 0.0564 | 0.9955 | 0.6841 |
| development | baseline | 1.0000 | 5646 | 0.7860 | 0.5723 | 0.3858 | 0.0419 | 0.9931 | 0.7997 |
| development | baseline | 2.0000 | 5689 | 0.7812 | 0.5927 | 0.3688 | 0.0385 | 1.0107 | 0.7641 |
| development | baseline | 3.0000 | 7219 | 0.8295 | 0.5671 | 0.3911 | 0.0418 | 0.9512 | 0.7187 |
| development | baseline | 4.0000 | 7296 | 0.7908 | 0.5775 | 0.3716 | 0.0510 | 0.9603 | 0.6854 |
| development | baseline | 5.0000 | 7262 | 0.8089 | 0.5804 | 0.3669 | 0.0528 | 0.9931 | 0.7296 |
| development | baseline_plus_historical_demand | 1.0000 | 5646 | 0.8018 | 0.5761 | 0.3744 | 0.0495 | 0.9797 | 0.7746 |
| development | baseline_plus_historical_demand | 2.0000 | 5689 | 0.7986 | 0.6003 | 0.3520 | 0.0478 | 1.0027 | 0.7375 |
| development | baseline_plus_historical_demand | 3.0000 | 7219 | 0.8390 | 0.5699 | 0.3839 | 0.0462 | 0.9389 | 0.6999 |
| development | baseline_plus_historical_demand | 4.0000 | 7296 | 0.8083 | 0.5811 | 0.3602 | 0.0587 | 0.9522 | 0.6640 |
| development | baseline_plus_historical_demand | 5.0000 | 7262 | 0.8247 | 0.5842 | 0.3570 | 0.0588 | 0.9867 | 0.7053 |

## 零事件变体

- development / no_volume / 5 日
- development / no_volume / 10 日
- development / no_volume / 20 日
- confirmation / no_volume / 5 日
- confirmation / no_volume / 10 日
- confirmation / no_volume / 20 日
