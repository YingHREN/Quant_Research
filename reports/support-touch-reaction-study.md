# 支撑区首触反应研究

- 数据截止：2026-07-24
- 首触事件：1462712
- 实际触达：1192109
- 模型权限：`advisory_only`
- 标签：首次触达后 3 个交易日内，失败优先于接受；未触达不进入反应率分母。
- 该研究不修改 Ridge、下行否决、最终决策策略或 UI。

## 研究门控

- `performance_condition_failed:acceptance_gain_at_least_2pp`
- `performance_condition_failed:at_least_2_group_wins`
- `causal_audit_failed`
- `future_holdout_required`

## 预注册性能条件（确认队列、10 日严格配对）

- 承接率增量：+0.50 pp
- 失效率变化：-1.29 pp
- 最大穿透 ATR 变化：-0.0232
- 改善时间折：5/5
- 改善板块组：1/1（要求覆盖 3 组且至少 2 组改善）
- 同向距离分箱：4/4
- 条件通过数：4/6

## 10 日严格配对（逐折）

| cohort | variant | fold | event_count | touch_rate | accepted_rate | failed_rate | ambiguous_rate | mean_maximum_rebound_atr | mean_maximum_penetration_atr |
|---|---|---|---|---|---|---|---|---|---|
| confirmation | baseline | 1.0000 | 6243 | 0.7793 | 0.5821 | 0.3764 | 0.0415 | 1.0065 | 0.7607 |
| confirmation | baseline | 2.0000 | 6145 | 0.7738 | 0.5971 | 0.3596 | 0.0433 | 1.0083 | 0.7664 |
| confirmation | baseline | 3.0000 | 7322 | 0.8062 | 0.5648 | 0.3944 | 0.0408 | 0.9587 | 0.7367 |
| confirmation | baseline | 4.0000 | 7110 | 0.7745 | 0.5814 | 0.3735 | 0.0450 | 1.0624 | 0.7231 |
| confirmation | baseline | 5.0000 | 6928 | 0.7822 | 0.5857 | 0.3656 | 0.0487 | 1.0054 | 0.7110 |
| confirmation | baseline_plus_historical_demand | 1.0000 | 6243 | 0.7985 | 0.5878 | 0.3623 | 0.0499 | 0.9905 | 0.7318 |
| confirmation | baseline_plus_historical_demand | 2.0000 | 6145 | 0.7897 | 0.5982 | 0.3499 | 0.0519 | 0.9930 | 0.7469 |
| confirmation | baseline_plus_historical_demand | 3.0000 | 7322 | 0.8229 | 0.5706 | 0.3821 | 0.0473 | 0.9482 | 0.7180 |
| confirmation | baseline_plus_historical_demand | 4.0000 | 7110 | 0.7968 | 0.5908 | 0.3555 | 0.0537 | 1.0506 | 0.6954 |
| confirmation | baseline_plus_historical_demand | 5.0000 | 6928 | 0.7998 | 0.5882 | 0.3557 | 0.0561 | 0.9898 | 0.6893 |
| development | baseline | 1.0000 | 5156 | 0.7775 | 0.5622 | 0.3974 | 0.0404 | 0.9734 | 0.8237 |
| development | baseline | 2.0000 | 5304 | 0.7787 | 0.6015 | 0.3622 | 0.0363 | 1.0104 | 0.7583 |
| development | baseline | 3.0000 | 6903 | 0.8336 | 0.5551 | 0.4046 | 0.0403 | 0.9439 | 0.7383 |
| development | baseline | 4.0000 | 6917 | 0.7928 | 0.5802 | 0.3682 | 0.0516 | 0.9645 | 0.6847 |
| development | baseline | 5.0000 | 6756 | 0.8111 | 0.5803 | 0.3653 | 0.0544 | 0.9823 | 0.7304 |
| development | baseline_plus_historical_demand | 1.0000 | 5156 | 0.7969 | 0.5702 | 0.3797 | 0.0501 | 0.9663 | 0.7907 |
| development | baseline_plus_historical_demand | 2.0000 | 5304 | 0.7949 | 0.6084 | 0.3446 | 0.0470 | 0.9963 | 0.7294 |
| development | baseline_plus_historical_demand | 3.0000 | 6903 | 0.8435 | 0.5598 | 0.3946 | 0.0455 | 0.9320 | 0.7147 |
| development | baseline_plus_historical_demand | 4.0000 | 6917 | 0.8095 | 0.5824 | 0.3570 | 0.0605 | 0.9570 | 0.6633 |
| development | baseline_plus_historical_demand | 5.0000 | 6756 | 0.8245 | 0.5837 | 0.3578 | 0.0585 | 0.9747 | 0.7132 |

## 零事件变体

- development / no_volume / 5 日
- development / no_volume / 10 日
- development / no_volume / 20 日
- confirmation / no_volume / 5 日
- confirmation / no_volume / 10 日
- confirmation / no_volume / 20 日
