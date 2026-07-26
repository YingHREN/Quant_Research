# TOPRISK-001 图表状态图层设计

日期：2026-07-26  
状态：待书面确认

## 目标

把现有 `high_level_distribution_risk_v1`（高位派发与顶部向下转折风险）从下方“各模型输出”卡片扩展到价格图和证券标题区，使用户能够：

1. 在历史 K 线上看到模型何时进入观察、高风险、确认和解除状态。
2. 通过现有“模型图层”筛选器独立开关 TOPRISK 标记。
3. 在证券标题附近直接看到最新 TOPRISK 状态与分数。
4. 保持图表清晰，不因风险状态持续多日而每天重复画标记。

本功能只展示既有模型结果，不改变 TOPRISK 的评分公式，也不改变统一预测决策策略。

## 现有能力与问题

TOPRISK 已经完成：

- 因果逐日计算；
- 高位背景、派发压力和结构破坏三个分项；
- 记忆、衰减与解除机制；
- 持久化到 forecast artifact 的 `risk_context`；
- 在所选日期的“各模型输出”区域展示；
- 被统一预测决策策略读取并用于降级或否决 Ridge 方向。

目前缺失：

- 历史状态切换没有 K 线标记；
- 模型图层筛选器没有 TOPRISK 选项；
- 页面顶部没有最新风险状态摘要；
- Web 层只能方便地取得一个日期的模型输出，不能直接取得精简的历史风险事件。

## 数据与服务设计

### 1. 复用预测风险缓存

在 `ForecastService` 增加只读的风险时间线方法。该方法：

- 使用与 `build()` 相同的 revision、覆盖范围、指纹和持久化 artifact；
- 不重新训练 Ridge；
- 不重新执行一套独立 TOPRISK 算法；
- 从已经生成或恢复的 `risk_context` 中切出指定股票的逐日 TOPRISK 字段；
- 返回 JSON-ready 的最新摘要和稀疏事件。

建议接口：

```python
ForecastService.build_top_risk_timeline(
    ticker,
    chart_dates,
    histories,
    *,
    expected_revision=None,
)
```

稳定返回结构：

```json
{
  "model_key": "high_level_distribution_risk_v1",
  "model_version": "v1",
  "status": "available",
  "latest": {
    "time": "2026-07-23",
    "score": 72.0,
    "state": "confirmed",
    "raw_state": "confirmed",
    "memory_age_sessions": 0
  },
  "events": [
    {
      "time": "2026-06-26",
      "type": "top_risk_watch",
      "score": 48.0,
      "state": "watch"
    }
  ]
}
```

当缓存、模型分组或数据不可用时，返回 typed unavailable 结果，而不是让股票页面失败。

为兼容测试注入的简化 Forecast Service，Flask Web 层先检查该方法是否存在。不存在时降级为 unavailable，不影响原股票详情响应。

### 2. 只输出状态切换事件

TOPRISK 有记忆，同一个状态可能持续多个交易日。图表不为每一天画点，只在以下状态切换时生成事件：

| 事件类型 | 触发条件 | 图表含义 |
|---|---|---|
| `top_risk_watch` | 从非活动状态进入 `watch` | 顶部风险开始值得观察 |
| `top_risk_high` | 首次进入 `high` | 派发或结构损伤已明显增强 |
| `top_risk_confirmed` | 首次进入 `confirmed` | 顶部向下转折风险得到确认 |
| `top_risk_recovery` | 模型的 recovery 条件触发，或从活动风险状态解除 | 风险已解除或显著缓和 |

`fading` 是风险记忆的延续，不单独画点；它通过顶部状态徽章和下方模型卡展示，避免图表噪声。

事件检测必须满足：

- 同一连续状态只出现一次；
- 事件只依赖当日及之前数据；
- 截断未来数据后，已有事件的日期和类型不改变；
- 事件日期必须存在于当前图表数据中。

### 3. 股票 API 载荷

股票详情响应新增：

```json
{
  "top_risk": {
    "model_key": "high_level_distribution_risk_v1",
    "model_version": "v1",
    "status": "available",
    "latest": {}
  }
}
```

TOPRISK 历史事件转换成图表统一 annotation，追加到：

```json
{
  "structures": {
    "annotations": [
      {
        "time": "2026-06-26",
        "type": "top_risk_watch",
        "label": "顶部风险观察",
        "score": 48.0
      }
    ]
  }
}
```

这样可复用现有 marker 渲染、图层过滤和日期坐标逻辑，不新增第二套图表协议。

## UI 设计

### 1. 模型图层筛选器

在现有可折叠“模型图层”筛选器增加：

- `高位派发/顶部风险`
- 内部 key：`top_risk`
- 默认关闭；
- “全部”预设包含它；
- “核心”预设仍保持 VCP、VCP 突破和 Pocket Pivot，不自动加入 TOPRISK。

默认关闭可以保持当前图表简洁，由用户主动查看卖出/风控信号。

### 2. K 线标记

四类事件共用一个图层开关，但颜色和形状不同：

- 观察：黄色圆点，K 线上方；
- 高风险：橙色向下箭头，K 线上方；
- 确认：红色向下箭头，K 线上方；
- 解除：绿色向上箭头，K 线下方。

标记只绑定已有交易日期，不添加未来时间点、不扩展价格范围、不创建 price line，因此不会引起图表抖动或横向拖动异常。

本地化文案必须明确方向，不使用含糊的“反转”：

- 顶部向下风险观察；
- 顶部向下高风险；
- 顶部向下风险确认；
- 顶部向下风险解除。

### 3. 最新状态徽章

证券标题区域增加一个紧凑徽章：

- `顶部风险 72 · 已确认`
- 状态颜色与图表事件一致；
- `fading` 显示为 `风险衰减中`；
- unavailable 显示为中性灰色 `顶部风险不可用`；
- 徽章不改变证券摘要卡的高度，避免页面布局跳动。

徽章是最新日期摘要；用户锁定历史日期后，下方“各模型输出”仍展示所选日期的完整证据，二者职责不同。

## 错误与性能边界

- TOPRISK 时间线失败不能使股票详情接口失败。
- 不在浏览器中重新计算模型。
- 不把完整风险 DataFrame 发送到浏览器，只发送最新摘要和状态切换事件。
- 缓存命中时只进行股票切片和事件压缩。
- 数据 revision 改变后沿用现有 invalidation 和 artifact 重建机制，时间线自动更新。
- 测试 Fake Service 没有时间线方法时保持向后兼容。

## 测试与验收

### Python

- 时间线从缓存 risk context 提取正确股票和日期；
- watch/high/confirmed/recovery 每次状态段只产生一个事件；
- `fading` 不产生重复事件；
- 前缀不变性；
- revision 不一致时与 forecast build 一样抛出 `ForecastRevisionChanged`；
- unavailable 路径类型稳定；
- 股票 API 正确合并 annotation；
- 注入旧 Fake Service 时 API 仍成功。

### JavaScript

- 图层定义从 9 个增加为 10 个；
- `top_risk` 默认关闭；
- “全部”包含、“核心”不包含；
- localStorage 选择可持久化；
- 关闭图层时四类事件均不渲染；
- 打开后使用正确文本、颜色、位置和形状；
- 徽章对 available、fading、confirmed、unavailable 正确显示。

### 浏览器验收

以 NBIS、MU、MRVL 至少各验证一次：

- 切换 TOPRISK 图层不会改变可视时间范围；
- 标记不会密集覆盖整张图；
- 日期锁定与拖动行为不受影响；
- 页面首次加载和切换股票无明显额外阻塞；
- 标记日期与下方所选日期模型输出一致。

## 非目标

- 本次不重新训练或调参 TOPRISK；
- 不改变 Ridge 收益预测；
- 不增加盘中逐笔数据；
- 不把 TOPRISK 设为默认可见图层；
- 不把风险事件解释为交易建议。
