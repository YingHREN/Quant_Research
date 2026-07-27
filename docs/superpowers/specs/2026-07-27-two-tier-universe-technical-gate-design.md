# 两层股票池与 CAN SLIM 技术门控设计

## 目标

将股票看板从当前主行情库约 194 只活跃证券扩展为两个语义明确的层次：

- **研究股票池**：读取 `research_prices.db` 的点时成员和日线，当前约
  1,014 个成员，用于横截面 RS、技术门控、板块筛选和研究排序。
- **活跃可操作池**：继续使用 `prices.db`，保留现有更新、完整预测、缓存预热
  和个股交互行为。

本阶段同时实现 CAN SLIM 四维门控中的“技术面门控”。扩入研究池不等于成为
正式候选；只有技术条件全部通过且数据充分的证券才显示为“技术面通过”。本阶段
不实现基本面、13F 机构面或完整大盘 Confirmed Uptrend 门控，因此不得把技术面
通过称为“四维正式候选”或直接买入信号。

## 设计选择

采用两层股票池，而不直接把 `prices.db` 替换成 `research_prices.db`：

1. 研究池使用轻量、只读、批量 SQL 计算最新技术快照，不执行逐股票完整历史
   VCP 重放或 Ridge 拟合。
2. 活跃池继续承担现有完整分析，避免 1,014 只证券让首屏、更新任务与预测缓存
   成本突然放大。
3. 用户选择仅存在于研究池的 ticker 时，详情页从研究库按需读取该股票和必要
   基准历史；该 ticker 默认只提供价格、技术门控、RS、分类和已有轻量因子。
   完整 Ridge、盘中订阅或更新队列若不支持，必须明确显示不可用原因，不能伪造。
4. 研究库不可用时，API 安全降级为现有活跃池，页面仍可使用。

直接替换主库会把更新、全市场预测和首屏冷启动成本同时扩大，风险过高；手工增加
少量 ticker 又会产生选择偏差，因此不采用。

## 数据来源和时点语义

### 研究池成员

研究池成员来自 `research_prices.db.universe_memberships`，并按观察日执行：

```sql
effective_from <= :asof
AND (effective_to IS NULL OR :asof < effective_to)
```

不得把数据库中存在日线但观察日不在成员区间内的证券自动加入研究池。当前状态
只使用观察日或观察日前最后一个有效交易日，禁止使用未来成员或未来价格。

### 价格数据

技术门控读取 `research_prices.db.daily_prices` 的复权 OHLCV。每个 ticker 的
观察日是研究池统一 `asof`；若该证券最后交易日早于 `asof`，门控状态为
`missing` 或 `stale`，不得在自己的旧日期上计算后冒充同日横截面结果。

### 横截面 RS 和分类

继续复用：

- `relative_strength_snapshots` 的 `cross_sectional_rs_v1`
- `sector_classifications`
- `ResearchRelativeStrengthService`
- `ResearchClassificationService`

RS 缺失不能被当作技术门控的中性值。RS 仍独立展示，但本阶段技术门控的硬条件
不包含 `RS >= 80`；UI 提供“技术面通过 + RS80/RS90”的组合筛选。这样可分别
评估趋势门控和相对强弱门槛，不把两个模型静默混成一个分数。

## 技术门控定义

新增独立、版本化的 `canslim_technical_gate_v1`。每项输出
`pass`、`fail` 或 `missing`，并保存实际值、阈值、观察日期和原因。

### 1. SMA50 趋势

- 计算最近 50 个完整交易日收盘价简单平均。
- `Close > SMA50` 为 `pass`，否则为 `fail`。
- 少于 50 个有效收盘价为 `missing`。

### 2. EMA10/EMA20 趋势栈

- 使用 `adjust=False` 的指数移动平均，最少分别需要 10/20 个有效收盘价。
- `EMA10 > EMA20` 为 `pass`，否则为 `fail`。
- 输出最近一次 EMA10/EMA20 上穿或下穿日期；若有效历史内未发生交叉，日期为
  `null`，不视为数据缺失。

### 3. 均线斜率

- EMA10、EMA20 分别输出最近 5 个交易日的百分比变化：
  `current / value_5_sessions_ago - 1`。
- 两条均线斜率都大于 0 才为 `pass`。
- 任一均线缺少当前值或 5 日前值时为 `missing`。

### 4. 距 52 周高点

- 52 周定义为截至观察日的最近 252 个完整交易日最高收盘价。
- 必须恰有至少 252 个有效收盘价，历史不足时为 `missing`，不得用较短窗口替代。
- 距高点不超过 20% 为硬门槛 `pass`；不超过 15% 额外输出
  `preferred=true`。
- 距高点计算为 `Close / High252 - 1`，因此值通常为 0 或负数。

### 汇总状态

四项硬条件是：

1. `close_above_sma50`
2. `ema10_above_ema20`
3. `moving_average_slopes_positive`
4. `within_20pct_of_52_week_high`

汇总规则：

- 任一项 `fail` → `technical_gate=fail`
- 无 `fail` 但任一项 `missing` → `technical_gate=missing`
- 四项全部 `pass` → `technical_gate=pass`

不生成 0～100 的伪概率。为了排序可额外输出 `passed_conditions/4`，但 UI 必须
标注为条件计数而非胜率。

## 后端架构

### `ResearchUniverseRepository`

在独立模块中提供只读研究池访问，避免让支持写入的 `MarketDataRepository`
同时理解两种 schema：

- `snapshot(asof=None) -> ResearchUniverseSnapshot`
- `load_history(ticker, asof=None) -> DataFrame`
- `contains(ticker, asof=None) -> bool`

`snapshot()` 用一组有界 SQL 读取有效成员及每只股票计算门控所需的最近 260 个
交易日，不把 235 万行完整历史加载进 Flask。返回内容包含成员状态、最新日期、
OHLCV 窗口和研究库 revision。

### `TechnicalGateService`

纯计算服务：

```python
evaluate(history, asof) -> TechnicalGateResult
evaluate_universe(histories, asof) -> dict[str, TechnicalGateResult]
```

它不访问数据库、不调用预测模型，便于逐日因果测试。版本号和阈值随输出返回。

### `UniverseSnapshotService`

保留现有活跃池路径，并增加研究池来源：

- API 默认返回两层摘要和计数。
- 活跃池行标记 `pool_membership.active=true`。
- 研究池行标记 `pool_membership.research=true`。
- 同一 ticker 只返回一行，合并两种 membership。
- 缓存键增加研究库 revision、研究池 asof 和技术门控版本。
- 研究库失败只移除研究扩展行，并在 `research_pool_status` 返回
  `unavailable`，不影响活跃池。

### 个股按需读取

`/api/stocks/<ticker>` 先查活跃主库；仅在返回 `UnknownTicker` 时检查研究库。
研究池 ticker 的分析上下文只加载自身、SPY、QQQ 和配置化板块基准。响应增加：

```json
{
  "data_scope": "research_only",
  "unsupported_outputs": [
    "live_update",
    "intraday",
    "full_market_ridge"
  ]
}
```

不能为研究池 ticker 触发主行情库写入或自动加入更新队列。

## API 与 UI

`/api/universe` 增加：

- `pool_summary.active_count`
- `pool_summary.research_count`
- `pool_summary.overlap_count`
- `research_pool_status`
- 每行 `pool_membership`
- 每行 `technical_gate`

股票池界面增加：

- “活跃池 / 研究池 / 全部”筛选
- “技术面通过 / 失败 / 数据缺失”筛选
- 可组合的 RS80、RS90 与板块筛选
- 技术门控状态徽章和 `通过条件数/4`
- 悬停或详情中的实际值、阈值、观察日、最近均线交叉日期

个股模型输出面板注册“CAN SLIM 技术面门控”诊断输出，但生命周期标为
`research`、权限标为 `diagnostic`。在基本面、机构面和大盘门控完成前，不生成
“正式候选”总状态。

中英文文案都需明确：

- 技术面通过不等于 CAN SLIM 四维通过
- 规则状态不是上涨概率
- 研究池 ticker 可能没有实时或完整预测支持

## 性能边界

- `/api/universe` 不得对 1,014 只逐一执行完整 `detect_vcp()` 历史扫描。
- 技术门控只读取最近 260 个交易日并进行向量化或线性窗口计算。
- 研究池 SQL 查询数量必须为常数，不随 ticker 数量线性增加。
- 冷构建目标不超过 5 秒，缓存命中目标不超过 250 毫秒。
- 研究池详情只按需读取选中 ticker 和少量基准，不加载全研究库历史。

若真实 1,014 只冷构建超过 5 秒，优先把技术快照物化到独立缓存表；不得通过减少
252 日窗口或使用未来预计算结果换取速度。

## 错误处理

- 研究数据库缺失、锁定或 schema 不兼容：活跃池继续可用，研究池状态
  `unavailable`。
- 历史不足、NaN、非正价格、过期数据：该 ticker 门控为 `missing`，并返回
  稳定原因码。
- 重复日期或非单调索引：拒绝计算，不自动排序或去重。
- RS、分类或技术门控之一不可用：各自独立显示缺失，不能相互代填。
- 研究池 ticker 不支持的模型：返回明确 unavailable 元数据，不抛出 500。

## 测试与验收

### 单元测试

- 精确验证四个条件及汇总三态逻辑。
- 验证 251 日不能计算完整 52 周指标，252 日可以。
- 验证历史追加未来数据不改变旧观察日结果。
- 验证最近交叉日期和 5 日斜率无前视。
- 验证 NaN、重复日期、非正价格和过期数据。

### 仓储和服务测试

- 观察日成员区间为半开区间且无未来成员泄漏。
- 研究池查询数量为常数。
- 研究库失败时活跃池安全降级。
- 同一 ticker 的双池 membership 正确合并。
- 研究池详情只加载自身和必要基准。
- 研究池 ticker 不进入主库更新队列。

### API 与前端测试

- 新旧 API 字段向后兼容。
- 池类型、技术状态、RS 和板块筛选可组合。
- 中文/英文名称、原因和限制完整。
- `missing` 不被渲染成 `fail` 或 `pass`。
- 选择研究池 ticker 时页面不崩溃，明确显示不支持的输出。

### 真实验收

- 记录研究池、活跃池、重叠池和技术门控三态数量。
- 抽查 NBIS、MU、AMD、MRVL 及至少五只新增研究池股票。
- 测量 1,014 只冷/热 `/api/universe` 时间。
- 验收前后记录三个价格数据库 SHA-256，确认只读路径未修改数据。
- 全量测试通过后，才在全局 TODO 中标记技术面门控及扩池子任务完成。

## 明确不在本阶段

- 点时 EPS、营收和年度盈利门控
- 13F 机构持仓门控
- Follow-through Day 与 Confirmed Uptrend 完整状态机
- 将四维门控接入最终买卖决策
- 为全部研究池股票预热 Ridge 或盘中订阅
- 自动把研究池证券写入 `prices.db`
