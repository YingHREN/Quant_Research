# 尾部方向误报匹配审计

> 离线研究；`online_authority=none`。不修改 Ridge、方向策略、风险否决权或 UI。

## 结论

- 没有特征通过冻结的准入门槛。
- 高分审计总体：42534 条。
- 一对一匹配：8199 对。
- 终点下跌、极端上涨、路径压力和普通样本使用互斥标签。

## 匹配覆盖

| scope_type | scope_name | case_count | matched_pair_count | unmatched_case_count | match_rate | unmatched_reason_count |
| --- | --- | --- | --- | --- | --- | --- |
| overall | all | 9405 | 8199 | 1206 | 0.8718 | 1206 |

## 特征证据

| feature | pair_count | case_availability | control_availability | standardized_difference | ci_low | ci_high | consistent_folds | consistent_large_groups | gate_passed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| atr20_pct | 8199 | 0.9957 | 0.9924 | 0.0367 | -0.1361 | 0.7652 | 3 | 3 | False |
| close_vs_ema20_pct | 8199 | 1.0000 | 1.0000 | -0.1286 | -4.1035 | -1.4429 | 5 | 3 | False |
| close_vs_sma200_pct | 8199 | 0.9019 | 0.8904 | -0.0794 | -13.6042 | -2.5534 | 5 | 3 | False |
| close_vs_sma50_pct | 8199 | 0.9778 | 0.9763 | -0.1034 | -6.6227 | -1.8575 | 4 | 3 | False |
| dollar_volume_ratio_20 | 8199 | 0.9949 | 0.9915 | -0.0142 | -1.4627 | 0.2189 | 3 | 3 | False |
| higher_low_confirmed | 8199 | 1.0000 | 1.0000 | -0.0075 | -0.0074 | 0.0035 | 4 | 3 | False |
| log_dollar_volume_20 | 8199 | 0.9949 | 0.9915 | -0.0195 | -0.2413 | 0.1146 | 3 | 2 | False |
| mom_12_1 | 8199 | 0.8744 | 0.8603 | 0.0344 | -0.1252 | 1.4290 | 2 | 1 | False |
| mom_3_1 | 8199 | 0.9712 | 0.9634 | -0.0234 | -0.1041 | 0.0425 | 3 | 1 | False |
| mom_6_1 | 8199 | 0.9426 | 0.9208 | 0.0345 | -0.0989 | 0.7130 | 2 | 2 | False |
| opening_gap | 8199 | 0.9991 | 0.9998 | -0.0045 | -0.0030 | 0.0027 | 3 | 3 | False |
| pivot_distance_pct | 8199 | 0.9957 | 0.9924 | -0.1459 | -3.8568 | -1.7590 | 5 | 3 | False |
| pressure_close_location | 8199 | 0.9923 | 0.9944 | -0.0536 | -0.0738 | -0.0130 | 5 | 3 | False |
| pressure_distribution_day | 8199 | 1.0000 | 1.0000 | 0.0168 | -0.0036 | 0.0179 | 4 | 3 | False |
| pressure_failed_breakout | 8199 | 1.0000 | 1.0000 | -0.0246 | -0.0212 | 0.0025 | 5 | 3 | False |
| pressure_signed_volume_proxy | 8199 | 0.9880 | 0.9868 | -0.0146 | -0.1477 | 0.0398 | 4 | 2 | False |
| pressure_upper_wick_ratio | 8199 | 0.9923 | 0.9944 | -0.0098 | -0.0154 | 0.0089 | 3 | 1 | False |
| prior_high_breakout | 8199 | 1.0000 | 1.0000 | -0.0408 | -0.0203 | -0.0056 | 4 | 3 | False |
| qqq_trend_state | 8199 | 1.0000 | 1.0000 | -0.0545 | -0.0657 | 0.0253 | 4 | 2 | False |
| realized_vol_63 | 8199 | 0.9837 | 0.9811 | -0.0008 | -0.0313 | 0.0372 | 4 | 2 | False |
| realized_volatility_change_20 | 8199 | 0.9732 | 0.9668 | 0.0010 | -0.0734 | 0.0649 | 3 | 2 | False |
| sector_relative_strength_20 | 8199 | 0.0870 | 0.0779 | -0.4129 | -0.0453 | -0.0116 | 4 | 3 | False |
| stock_sector_relative_strength_20 | 8199 | 0.0864 | 0.0772 | -0.2382 | -0.0869 | -0.0288 | 5 | 3 | False |
| strict_vcp | 8199 | 0.9735 | 0.9683 | -0.0114 | -0.0036 | 0.0018 | 2 | 2 | False |
| tight_platform | 8199 | 0.9735 | 0.9683 | -0.0198 | -0.0073 | 0.0011 | 4 | 3 | False |
| trendline_breakout | 8199 | 1.0000 | 1.0000 | 0.0220 | -0.0006 | 0.0099 | 5 | 3 | False |
| volume_change | 8199 | 0.9960 | 0.9973 | 0.0118 | -0.0652 | 0.5541 | 3 | 3 | False |
| volume_ratio | 8199 | 0.9957 | 0.9924 | -0.0152 | -0.0760 | 0.0319 | 3 | 3 | False |

## 数据边界

- 财报临近：不可用；点时财报日历当前覆盖率为零。
- 真实市值：不可用；成交额只作为流动性代理。
- 所有未来收益保持原始、未截尾，且不参与观察日特征。
