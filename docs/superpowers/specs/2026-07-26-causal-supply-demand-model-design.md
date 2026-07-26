# 因果日线供需模型设计

## 目标

建立两个彼此独立、可审计的日线规则模型：

- `supply_pressure_v1`：识别上涨或整理过程中的疑似供给增加、价格拒绝和结构转弱。
- `demand_confirmation_v1`：识别成交参与、价格承接、卖方衰竭和突破跟随。

两个模型只使用观察日收盘前可见的 OHLCV、QQQ 和板块代理数据。输出是规则证据和 0～100 分的研究评分，不是概率，也不能证明真实机构、主动买盘或主动卖盘。第一版不修改 Ridge 预测值，不取得最终决策否决权。

## 方案选择

采用独立的 `research/supply_demand.py`，复用 `research.market_pressure.build_pressure_rows()` 的原子指标，但不把组合评分继续堆入 `market_pressure.py` 或 TOPRISK。

选择独立模块有三个原因：

1. 供给和需求可以分别回测，不能被迫互为相反数。
2. TOPRISK 可以消费供给输出，但高位顶部语义不会污染通用供需模型。
3. 模型注册和 UI 只消费稳定的输出契约，不需要重新实现金融规则。

## 输入与时点

主接口：

```python
build_supply_demand_rows(
    history: pd.DataFrame,
    *,
    qqq_close: pd.Series | None = None,
    sector_close: pd.Series | None = None,
) -> pd.DataFrame
```

`history` 必须包含唯一、递增交易日索引及 `Open`、`High`、`Low`、`Close`、`Volume`。QQQ 和板块序列按交易日左连接，不进行未来填充。所有滚动基准只使用当日及此前数据；凡是“此前均值”“此前压力位”均先 `shift(1)` 再滚动。

输出的时点语义固定为 `close_confirmed`。如果观察日数据不完整，相关证据为不可用，不用零值冒充中性。

## 原子证据

### 供给模型

供给证据分为三个独立组，每组封顶，防止同一根放量弱势 K 线重复获得多次满分。

#### 成交与收盘组，最高 40 分

- `distribution_day`：当日下跌、成交量比率至少 1.2，收盘位置不高于 -0.4。
- `high_volume_non_progress`：成交量比率至少 1.5，绝对收盘涨跌不超过 0.5%。
- `negative_signed_volume`：收盘位置乘成交量比率不高于 -0.75。
- `distribution_cluster`：近 10 日至少两次派发日。

原始权重依次为 25、15、15、15 分；该组即使多个条件同时满足也不超过 40 分。

#### 价格拒绝组，最高 30 分

- `upper_wick_supply`：成交量比率至少 1.2，上影线占真实日内振幅至少 35%。
- `failed_breakout`：盘中高点突破此前 20 日压力，但收盘重新落回压力下方。
- `repeated_failed_breakout`：近 10 日至少两次真实突破失败。
- `pressure_test_efficiency_decay`：当日最高价距此前 20 日压力不超过 2% 时视为一次压力测试；和此前 15 日最近一次测试相比，当日成交量比率不低于前次，但正向收盘涨幅除以成交量比率后的推进效率下降至少 30%。

原始权重依次为 15、20、10、10 分，组分不超过 30 分。

#### 结构与环境组，最高 30 分

- `volume_confirmed_ema20_break`：收盘从 EMA20 上方跌到下方，成交量比率至少 1.2。
- `relative_strength_breakdown_qqq`：个股 20 日收益落后 QQQ 至少 3 个百分点。
- `relative_strength_breakdown_sector`：个股 20 日收益落后板块至少 3 个百分点。
- `weak_rebound_below_ema20`：此前五日内曾跌破 EMA20，当前反弹成交量比率不高于 0.8，且收盘仍未站回 EMA20。

原始权重依次为 15、10、10、15 分，组分不超过 30 分。缺少 QQQ 或板块代理时，对应证据不可用并降低覆盖率，另一个代理仍可独立计算。

第一版不尝试拟合主观上涨趋势线，避免与现有趋势线交互问题耦合。上涨支撑线破位保留给持仓感知的 `EXIT-001`。

### 需求模型

需求证据同样分为三个独立组。

#### 成交参与组，最高 35 分

- `positive_signed_volume`：收盘位置乘成交量比率至少 0.75。
- `up_volume_confirmation`：当日上涨、成交量比率至少 1.2，收盘位置至少 0.4。
- `strong_close`：收盘位置至少 0.6。

原始权重依次为 15、20、10 分，组分不超过 35 分。

#### 承接与吸收组，最高 35 分

- `seller_exhaustion`：当日成交量比率至少 1.8，最低价不低于此前五日最低价的 99.5%，并收在日内中点以上，表示大量成交没有继续推动价格向下。
- `buyer_absorption`：盘中低点低于此前五日最低价，但收盘重新站回该低点之上，收盘位置至少 0.4，成交量比率至少 1.2。
- `low_volume_higher_low`：前一日为下跌回调，成交量比率不高于 0.8，前一日低点高于其此前 10 日最低价；当日收盘再高于前一日收盘时确认。

原始权重依次为 15、20、15 分，组分不超过 35 分。

卖方衰竭和买方吸收均为日线价格/成交量代理，不声称识别真实逐笔主动方向。

#### 突破与环境组，最高 30 分

- `breakout_acceptance`：收盘高于此前 20 日压力，成交量比率至少 1.2，且收盘位于突破价上方 0～5%。
- `breakout_follow_through`：此前三日出现 `breakout_acceptance`，当前收盘仍高于当时冻结压力位且未出现放量弱收盘。
- `relative_strength_confirmation_qqq`：个股 20 日收益领先 QQQ 至少 2 个百分点。
- `relative_strength_confirmation_sector`：个股 20 日收益领先板块至少 2 个百分点。

原始权重依次为 15、10、10、10 分，组分不超过 30 分。

## 评分、覆盖率与状态

每个证据组先计算组内原始分，再按组上限截断。模型总分为三个组分之和，范围 0～100。

每个模型同时输出 `coverage`。覆盖率按可计算原子证据的原始权重占全部原始权重计算；组上限只用于分数去重，不改变覆盖率分母。覆盖率低于 75% 时总分为 `None`，状态为 `unavailable`。缺少 QQQ 或板块时，只降低环境组覆盖率，不让整个模型静默失败。

综合状态由供给分和需求分共同决定：

| 状态 | 条件 |
|---|---|
| `healthy_advance` | 需求分至少 60，供给分低于 40 |
| `two_way_contest` | 需求分至少 50，供给分至少 50 |
| `distribution_risk` | 供给分至少 60，需求分低于 50 |
| `low_participation` | 两个分数均低于 40 |
| `mixed` | 其他可用组合 |
| `unavailable` | 任一核心模型覆盖率低于 75% |

状态只是简洁展示层；UI 必须同时显示两个独立分数，不能只显示综合状态。

## 输出契约

每个日期至少输出：

- `supply_pressure_score`
- `supply_pressure_coverage`
- `supply_pressure_conditions`
- `supply_close_volume_score`
- `supply_rejection_score`
- `supply_structure_context_score`
- `demand_confirmation_score`
- `demand_confirmation_coverage`
- `demand_confirmation_conditions`
- `demand_participation_score`
- `demand_absorption_score`
- `demand_breakout_context_score`
- `supply_demand_state`
- `unavailable_reasons`

条件字段为稳定、去重的字符串元组。所有数值只包含有限浮点数或 `None`。

## 模型注册与 UI

注册两个正式但仅供参考的模型输出：

- `supply_pressure_v1` 放在“向下风险”组，类型为 `rule_score`，权限为 `advisory`。
- `demand_confirmation_v1` 放在“向上结构”组，类型为 `rule_score`，权限为 `advisory`。

输出面板显示分数、覆盖率、综合状态、已满足条件及限制说明。中文使用“疑似供给压力”和“需求确认代理”，英文使用 “Supply pressure proxy” 和 “Demand confirmation proxy”。

不新增 K 线覆盖图层、延伸线或价格线，避免改变图表自动缩放、拖拽和日期锁定。第一版只进入固定高度的模型输出面板。

## 错误与不可用处理

- OHLCV 列缺失、索引重复或价格关系非法时抛出明确输入异常。
- 历史不足窗口时返回不可用证据和覆盖率，不返回伪造的零分。
- 外部 QQQ/板块序列缺失或日期不齐时仅降低对应环境证据覆盖率。
- 单个模型输出构建失败时沿用模型注册表的局部降级机制，不影响 Ridge 和其他输出。

## 测试与验收

实现必须覆盖：

1. 放量下跌、放量滞涨和弱收盘同时出现时，成交与收盘组不超过 40 分。
2. 突破失败与真正放量突破不会混淆。
3. 卖方衰竭、买方吸收、低量更高低点分别有正反例。
4. 供给高和需求高能够形成“双向放量争夺”，而不是互相抵消。
5. 缺少历史、QQQ 或板块时覆盖率和不可用原因准确。
6. 追加任意未来行情后，已有日期的全部原子证据、分数和状态完全不变。
7. API 与模型注册输出包含稳定身份、时点语义、限制和中英文解释。
8. UI 更新不新增图表 series，不改变可视范围和价格轴。

完成实现不代表模型获得最终决策权。供给、需求和组合状态必须在扩展股票池的固定走步实验中分别报告精确率、召回率、未来最大不利波动、收益和换手；未经样本外验证只能保持 `advisory`。
