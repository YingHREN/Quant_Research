# 未验证 Ridge 方向展示设计

## 目标

在不改写 Ridge 原始预测收益、原始方向或
`forecast_decision_policy` 计算逻辑的前提下，停止把尚未超过固定基线的
方向展示成已验证的可靠预测。第一验收对象是 5 日 Ridge；同一展示契约
适用于其他周期，避免不同周期出现相互矛盾的可靠性语义。

## 当前问题

API 已提供 `forecast_evaluation.evidence_status`，模型详情中也能看到
“尚未证明超过基线”，但 Ridge 注册表仍标记为 `production`，卡片摘要
只显示 `available`。用户首先看到的是“上涨/下跌”和“可用”，容易把
“模型成功计算”误解成“方向已经验证”。最终决策卡也没有并列显示主预测
证据状态。

## 选择方案

采用“保留原始预测 + 醒目降级语义”：

- 始终保留 `predicted_return`、`raw_direction`、训练样本和训练截止日；
- Ridge 模型生命周期改为 `research`，表示当前实现可以运行，但尚未获得
  生产方向证据；
- 根据对应周期的点时评估输出独立 `direction_reliability`：
  `proven`、`unproven`、`insufficient`、`not_precomputed` 或
  `unavailable`；
- 卡片摘要在状态徽章旁增加方向可靠性徽章。`unproven` 和
  `insufficient` 使用醒目但不等同于报错的研究色；
- 最终决策卡并列显示主预测的证据状态，并明确“最终方向可能包含风险规则
  调整，不代表 Ridge 方向已验证”；
- 不隐藏、不置零、不强制中性，也不修改向下风险否决权。

不采用直接隐藏方向，因为会破坏历史复盘；不采用强制中性，因为会把展示
层可靠性问题错误地变成策略规则。

## API 合约

`model_outputs.primary` 的 Ridge 输出新增：

```text
direction_reliability
```

映射规则：

```text
预测不可用                         -> unavailable
evidence_status = proven           -> proven
evidence_status = unproven         -> unproven
evidence_status = insufficient     -> insufficient
缺少或 not_precomputed             -> not_precomputed
未知值                             -> not_precomputed
```

`model_outputs.decision` 新增相同的
`primary_direction_reliability`，仅作为解释字段，不参与策略计算。
旧缓存或旧 API 缺少字段时，前端回退为不显示新徽章，不制造“已验证”。

模型注册表中 `ridge_direction_v1.lifecycle` 改为 `research`；
`decision_permission` 继续为 `informational`。

## UI

- Ridge 卡片摘要同时展示计算状态和方向可靠性；
- 详情中的原始方向标签改为“原始 Ridge 方向”，继续展示原值；
- 可靠性字段使用独立中英文文案：
  “方向已超过基线”“方向尚未证明超过基线”“评估样本不足”
  “尚未预计算历史评估”“预测不可用”；
- 最终决策卡显示“主预测可靠性”，并保留原始方向与最终方向并列；
- 徽章不改变卡片高度、图表尺寸、价格轴、拖拽、缩放或日期锁定。

## 测试

先写失败测试，再实现：

1. 5 日评估为 `unproven` 时，API 保留原始上涨方向和收益，同时输出
   `direction_reliability=unproven`；
2. `proven`、`insufficient`、缺失评估和预测不可用映射正确；
3. Ridge 注册表生命周期为 `research`，权限仍为 `informational`；
4. 最终决策输出携带相同可靠性，但原始/最终方向不改变；
5. 中英文卡片摘要包含可靠性文案；
6. 旧 payload 缺少新字段时不报错；
7. 现有 API、日期锁定和图表交互测试保持通过。

## 非目标

- 不重新训练 Ridge；
- 不改变预测阈值、收益值或方向；
- 不把 Logistic 挑战模型接入线上；
- 不修改风险覆盖规则；
- 不将评估准确率解释为单次预测概率。
