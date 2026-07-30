# 底部状态因果评估

> 仅供研究；不构成投资建议，不改变线上决策权限。

## 确认队列核心结果

确认队列 10 日匹配事件 163 个；上涨率增量 +3.68 pp；平均收益增量 +2.45 pp；MAE 差值 +2.13 pp。

## 消融与覆盖

评估变体：full、no_location、no_exhaustion、no_demand、no_structure、no_environment。
存在零正向事件的变体：无。

## 研究门槛

当前可晋级：否。
失败关闭原因：positive_rate_gain_below_5pp、insufficient_group_evidence、stage_monotonicity_failed、insufficient_ablation_advantage、group_causal_audit_failed、future_holdout_required。

历史板块回填仍可能包含非因果假设；未来时间外留出通过前，模型始终保持 advisory_only。
