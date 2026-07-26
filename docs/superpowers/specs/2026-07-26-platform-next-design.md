# 量化工作台下一阶段工程设计

日期：2026-07-26
状态：用户已批准连续实现五项，并要求完成前不再询问

## 目标

连续交付五个相互独立但契约兼容的模块：

1. 在网页显示预测缓存命中、重建、生成时间、行情日期和算法版本。
2. 用注册接口组装“各模型输出”，后续模型不再改写核心分组函数。
3. 合并同日同侧图表标记，降低 VCP、Pocket Pivot 与结构信号遮挡。
4. 基于扩充研究池计算真实同日横截面 RS Rating，并在股票池展示和筛选。
5. 对 TOPRISK、现有风险记忆、Ridge 及组合策略执行统一点时对照评估。

所有新增研究结果继续遵守点时因果原则，不把规则分数解释为概率，不因一次实验自动升级生产模型权限。

## 方案比较

### 方案 A：请求时动态计算全部内容

每次打开股票时查询缓存库、扫描扩充研究价格、组装模型并运行风险评估。实现直接，但会重新引入网页卡顿，并让 UI 请求与研究实验耦合。

### 方案 B：离线预计算研究数据，在线读取轻量状态（采用）

缓存状态由服务内遥测和 SQLite 元数据提供；RS 由独立构建命令写入派生快照表；TOPRISK 评估由独立命令输出 JSON/Markdown。网页请求只读取少量元数据和派生行。该方案保持现有按需详情性能，也方便审计和重复实验。

### 方案 C：引入后台任务队列和独立分析服务

长期扩展性最好，但当前是单机 Flask 工作台，引入队列、worker 和新存储边界超出本轮必要范围。

## 模块一：缓存可观测性

### 后端契约

`ForecastArtifactStore` 提供只读 `status()`，返回：

- `state`: `ready`、`empty`、`unavailable`
- `entry_count`
- `latest_created_at`
- `market_asof`
- `model_key`
- `model_version`
- `feature_version`
- `risk_context_version`
- `format_version`
- `size_bytes`

`ForecastService.cache_status()` 在此基础上增加：

- `last_access`: `memory_hit`、`disk_hit`、`rebuilt`、`miss`、`unavailable`
- `database_revision`
- `memory_ready`
- `build_started_at`
- `build_finished_at`

构建期间状态为 `rebuilding`。内部文件路径、异常文本和 checksum 不暴露给浏览器。

新增只读接口 `GET /api/cache/status`。接口失败时返回安全的 `unavailable` 状态，不影响股票池和单股接口。

### UI

顶部状态区增加可折叠“预测缓存”状态。正常时显示“已命中/已就绪”，重建时显示“正在重建”，并展示行情日期、生成时间和模型版本。页面初始化时读取一次，行情更新结束后再次读取；不增加持续高频轮询。

## 模块二：模型输出注册接口

新增 `ModelOutputRegistry` 与不可变 `ModelOutputRegistration`：

- 唯一 `key`
- `family`: `primary`、`downside`、`bullish_structure`
- 稳定 `order`
- `builder(context) -> mapping`

`ModelOutputContext` 保存 forecast、chart row 和 evaluation 的只读副本。默认注册表按现有顺序注册全部生产、研究和计划模型。`build_model_outputs()` 只负责调用注册表、隔离单个 builder 失败、校验 key 一致性并组装最终决策。

要求：

- 重复 key 立即拒绝。
- family 非法立即拒绝。
- 单个扩展 builder 失败时该模型输出 `unavailable`，不得使整个预测失败。
- 默认 API JSON 顺序和字段保持兼容。
- 决策策略仍由 `forecast_decision_policy` 生成，不允许展示注册器修改决策。

## 模块三：图表标记防重叠

新增纯函数 `layoutChartMarkers(markers)`：

- 先按日期、位置和显式优先级稳定排序。
- 同一日期且同为 `aboveBar` 或同为 `belowBar` 的多个标记合并为一个。
- 合并文本去重，使用 ` · ` 连接。
- 合并标记保留最高优先级信号的颜色和形状。
- 预测起点标记不与研究形态标记合并。
- 不创建未来日期，不改变价格轴、时间轴或拖拽锁定。

优先级从高到低：确认风险/放量突破、VCP/Pocket Pivot、结构反转、辅助结构点。图层筛选发生在合并之前，因此用户隐藏的模型不会出现在合并标签中。

## 模块四：真实横截面 RS Rating

### 口径

在观察日 `t`，对每只至少有 253 个有效交易日的普通股计算：

- `r63 = close_t / close_t-63 - 1`
- `r126 = close_t / close_t-126 - 1`
- `r189 = close_t / close_t-189 - 1`
- `r252 = close_t / close_t-252 - 1`
- `composite = 0.40*r63 + 0.20*r126 + 0.20*r189 + 0.20*r252`

在同一观察日、同一合格研究股票池中对 `composite` 做百分位排名，映射为 `1..99` 的 `rs_rating`。最近季度权重更高，但该指标仍明确标注为本项目的 `cross_sectional_rs_v1`，不冒充 IBD 专有评分。

### 存储与构建

`build_research_rs.py` 从 `research_prices.db` 的复权收盘读取指定观察日之前的必要窗口，写入派生表 `relative_strength_snapshots`。主键为 `(asof_date, ticker, model_version)`，保存各周期收益、综合值、rating、同日样本数和生成时间。

构建使用事务替换同一日期同一版本的完整快照；不足历史的股票不产生评分。原始 `daily_prices` 不修改。

`ResearchRelativeStrengthService` 只批量读取最新匹配快照并合并到 `/api/universe`，数据库或快照缺失时返回 `unavailable`，不回退到旧 Sigmoid 动量近似值。股票池新增 RS 排序和 `RS ≥ 80`、`RS ≥ 90` 筛选，详情展示版本、日期和同日样本数。

## 模块五：TOPRISK 对照评估

新增统一评估模块，按每个 ticker/date 生成以下信号：

- `ridge_down`: Ridge 原始方向为下跌。
- `immediate_8`: 当日即时风险分达到生产阈值。
- `memory_12`: 个股记忆风险达到高阈值。
- `toprisk_confirmed`: TOPRISK 原始状态确认。
- `toprisk_stateful`: TOPRISK 状态为 high/confirmed/fading。
- `ridge_plus_toprisk`: 当前生产决策最终方向为下跌。

未来路径标签：

- 5、10、20 日终点收益。
- 5、10、20 日最大不利波动（MAE）。
- 默认风险事件为 MAE 不高于 `-5%`；报告同时保存阈值。

每个信号报告：

- 样本数、信号数、覆盖率
- precision、recall、specificity、balanced accuracy
- 平均终点收益、平均 MAE
- 事件提前量（首次信号到首次达到风险阈值的交易日数）
- 误报率

分组至少包含：

- all
- semiconductor
- software
- other

市场阶段使用已有可用状态时分组；不可用时明确标记，不自行用未来数据补造阶段。命令输出机器可读 JSON 和 Markdown 表格。实验读取固定输入快照和模型版本，不自动修改生产阈值或决策权限。

## 错误处理与降级

- 缓存状态读取失败只影响状态徽章。
- 注册模型 builder 失败只使该模型不可用。
- RS 派生表缺失时保留原股票池，RS 显示不可用。
- 标记布局遇到未知标记时保留该标记，并采用最低优先级。
- 评估中历史不足的尾部样本剔除并计入不可成熟数量。
- 所有网页错误使用稳定代码和本地化安全文案，不暴露本地路径。

## 测试与验收

- 每个新接口先写失败测试，完成红—绿循环。
- 缓存状态覆盖空库、命中、重建、损坏和无存储。
- 注册器覆盖顺序、重复 key、失败隔离及默认输出兼容。
- 标记布局覆盖同日同侧合并、上下侧分离、图层筛选和预测标记独立。
- RS 覆盖因果窗口、同日排名、历史不足、事务替换和服务降级。
- TOPRISK 评估覆盖未来尾部排除、混淆矩阵、提前量、分组及输出可复现性。
- 完整 Python 测试通过，并在本地真实研究库生成 RS 快照和 TOPRISK 报告。
- 浏览器验证缓存状态、RS 筛选、模型输出和图标布局，控制台无错误。

## 非目标

- 本轮不接入付费基本面、13F 或逐笔成交。
- 不复制 IBD 私有算法或名称。
- 不自动把实验最佳阈值写回生产。
- 不为图标引入会影响坐标轴的额外价格序列。
- 不把缓存数据库作为研究结果唯一副本。
