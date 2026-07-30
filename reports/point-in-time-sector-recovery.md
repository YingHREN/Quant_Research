# 历史时点板块特征覆盖恢复审计

> 离线研究；`online_authority=none`。不修改 Ridge、策略、风险否决权或 UI。

## 结论

- 没有方向特征通过冻结的全部准入门槛。
- 固定匹配样本：8199 对；未重新匹配。
- 月末价格行为分类从下一股票交易日起生效，最多使用 45 天。
- 20 日相对收益要求股票、板块 ETF 与 QQQ 使用完全相同日期。

## 特征证据

| feature | pair_count | case_availability | control_availability | standardized_difference | ci_low | ci_high | minimum_fold_pair_availability | final_gate_passed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| pit_sector_assignment_age_days | 8199 | 0.9390 | 0.9191 | -0.0100 | -1.2341 | 1.0336 | 0.6789 | False |
| pit_sector_relative_strength_20 | 8199 | 0.9390 | 0.9191 | -0.1024 | -0.0106 | -0.0030 | 0.6789 | False |
| pit_sector_residual_correlation | 8199 | 0.9390 | 0.9191 | 0.0732 | 0.0046 | 0.0234 | 0.6789 | False |
| pit_stock_sector_relative_strength_20 | 8199 | 0.9390 | 0.9191 | -0.0723 | -0.0818 | -0.0133 | 0.6789 | False |

## 逐折覆盖

| feature | fold | pair_count | both_available_count | pair_availability |
| --- | --- | --- | --- | --- |
| pit_sector_relative_strength_20 | 1 | 1179 | 1041 | 0.8830 |
| pit_sector_relative_strength_20 | 2 | 928 | 630 | 0.6789 |
| pit_sector_relative_strength_20 | 3 | 1704 | 1589 | 0.9325 |
| pit_sector_relative_strength_20 | 4 | 1348 | 1173 | 0.8702 |
| pit_sector_relative_strength_20 | 5 | 3040 | 2714 | 0.8928 |
| pit_stock_sector_relative_strength_20 | 1 | 1179 | 1041 | 0.8830 |
| pit_stock_sector_relative_strength_20 | 2 | 928 | 630 | 0.6789 |
| pit_stock_sector_relative_strength_20 | 3 | 1704 | 1589 | 0.9325 |
| pit_stock_sector_relative_strength_20 | 4 | 1348 | 1173 | 0.8702 |
| pit_stock_sector_relative_strength_20 | 5 | 3040 | 2714 | 0.8928 |
| pit_sector_assignment_age_days | 1 | 1179 | 1041 | 0.8830 |
| pit_sector_assignment_age_days | 2 | 928 | 630 | 0.6789 |
| pit_sector_assignment_age_days | 3 | 1704 | 1589 | 0.9325 |
| pit_sector_assignment_age_days | 4 | 1348 | 1173 | 0.8702 |
| pit_sector_assignment_age_days | 5 | 3040 | 2714 | 0.8928 |
| pit_sector_residual_correlation | 1 | 1179 | 1041 | 0.8830 |
| pit_sector_residual_correlation | 2 | 928 | 630 | 0.6789 |
| pit_sector_residual_correlation | 3 | 1704 | 1589 | 0.9325 |
| pit_sector_residual_correlation | 4 | 1348 | 1173 | 0.8702 |
| pit_sector_residual_correlation | 5 | 3040 | 2714 | 0.8928 |
