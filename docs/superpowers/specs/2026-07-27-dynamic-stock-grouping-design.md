# 全股票动态分组与板块上下文设计

## 目标

把当前依赖少量硬编码股票名单的模型分组，升级为覆盖研究库全部股票的动态、可追溯分组体系。每只股票必须拥有：

- 一个基本面大盘板块；
- 一个对应的板块 ETF；
- 零个或多个主题组；
- 可选的人工覆盖记录；
- 分类来源、规则版本、生效日期和置信度。

新增股票进入研究库时必须同步执行分类。无法可靠归类的股票进入显式的 `unclassified_review` 待复核状态，不得静默落入科技板块或绕过板块模型。

SNDK 归入半导体主题，并同时保留科技大板块与 XLK 上下文。其顶部派发模型使用 SOXX/SMH 主题上下文、XLK 大板块上下文以及 QQQ/SPY 市场上下文。

## 现状与问题

研究库已经保存两套基础分类：

- `sec_sic_v1`：按 SEC SIC 得到 11 个基本面板块；
- `market_behavior_v1`：按行情相关性得到交易行为板块和参考 ETF。

但预测系统的 `web/market_groups.py` 仍只维护半导体、AI 基础设施和软件的固定 ticker 名单。因此：

- 大多数研究池股票虽然在数据库中有板块信息，却不能进入板块风险模型；
- 新加入股票不会自动获得模型分组；
- SNDK 已被分类为科技板块，但不在半导体硬编码名单中，导致顶部风险时间线不可用；
- 分类 UI、市场板块页面和预测风险模型使用的成员口径不完全一致。

## 方案选择

采用“基本面主分类 + 行情校验 + 主题规则 + 人工覆盖”的混合方案。

不采用纯 SIC 方案，因为存储、AI 基础设施和跨行业公司容易落入过宽的大板块；不采用纯价格相关性方案，因为分类会随行情漂移，且若处理不当会产生时间穿越。

分类优先级固定为：

1. 生效中的人工覆盖；
2. SEC SIC 精确行业或主题规则；
3. SEC SIC 宽范围板块规则；
4. 仅在 SEC 分类不可用时，使用满足覆盖率和置信度门槛的行情分类；
5. `unclassified_review`。

行情分类始终保留为独立证据。它与基本面分类冲突时，系统显示冲突，不静默覆盖基本面主分类。

## 分组数据契约

新增统一的 `GroupAssignment` 契约：

```text
ticker
asof
sector_key
sector_benchmark
theme_keys[]
theme_benchmarks{}
primary_model_group
classification_state
source
rule_version
confidence
override_reason
```

约束如下：

- `sector_key` 必须是 11 个标准板块之一，或 `unclassified_review`；
- 已分类股票必须有且仅有一个 `sector_benchmark`；
- `theme_keys` 可以为空，但不能重复；
- `primary_model_group` 优先使用具体主题，否则使用基本板块；
- 所有字段按 `asof` 点时解析，不读取未来分类；
- 同一 ticker、同一生效区间不能存在冲突覆盖。

11 个标准板块继续关联：

| 板块 | ETF |
| --- | --- |
| technology | XLK |
| communication | XLC |
| consumer_discretionary | XLY |
| consumer_staples | XLP |
| energy | XLE |
| financials | XLF |
| health_care | XLV |
| industrials | XLI |
| materials | XLB |
| real_estate | XLRE |
| utilities | XLU |

首批主题：

- `semiconductor` → SOXX、SMH；
- `software` → IGV、XSW；
- `ai_infrastructure` → 半导体主题的关联组，但 UI 不称为半导体成分。

SNDK 的覆盖记录为：

```text
sector_key: technology
sector_benchmark: XLK
theme_keys: [semiconductor]
primary_model_group: semiconductor
reason: flash memory and storage semiconductor exposure
```

## 组件边界

### 分类规则注册表

集中维护：

- 标准板块和 ETF 映射；
- SIC 到板块的规则；
- SIC、行业描述到主题的规则；
- 人工覆盖记录；
- 规则版本。

人工覆盖使用受 Git 管理的版本化数据文件，包含生效日期和原因。数据库构建时把覆盖结果写入分类快照，以保证本地数据库可独立审计。

### 分组解析服务

新增只读解析服务，按 ticker 和 `asof` 批量返回 `GroupAssignment`。服务只读取分类元数据，不加载日线；使用数据库 revision 做有界缓存。

解析服务负责优先级、冲突和待复核状态，不负责计算收益或风险分数。

### 动态模型分组

保留 `MarketGroup` 作为板块/主题定义，不再在其中保存完整固定成员名单。模型运行时根据同一观察日的 `GroupAssignment` 生成成员快照。

每只股票同时获得三层外部上下文：

1. SPY/QQQ 市场上下文；
2. 11 大板块 ETF 上下文；
3. 可用时的主题 ETF 和主题成员上下文。

为避免同一股票在多个组被重复计算，风险模型只使用一个 `primary_model_group` 生成成员风险状态，同时把大板块与主题上下文作为不同字段保留。决策模型可以分别展示：

- `market_risk_score`；
- `sector_risk_score`；
- `theme_risk_score`；
- `individual_risk_score`。

缺少主题时不影响基本板块风险；缺少板块 ETF 时明确降级为个股和市场证据，不重新分配权重。

## 新股票自动分类

研究库导入流程增加强制分类阶段：

```text
证券身份与 SEC SIC
  -> 基本板块分类
  -> 主题规则
  -> 人工覆盖
  -> 行情分类校验
  -> GroupAssignment 快照
  -> 覆盖率审计
  -> 才允许发布新的研究库 revision
```

导入验收要求：

- 每个活跃普通股都有一条当前分组记录；
- 分类结果不是标准板块时必须为 `unclassified_review`；
- 每个标准板块都有有效 ETF；
- 所有主题都有有效参考 ETF；
- 新股票缺少 SEC 身份或历史不足时仍保留待复核状态和原因；
- 分类失败不能导致整批价格数据丢失，但不能发布为“完整分类”。

## API 与 UI

股票池和个股 API 增加统一分组字段。UI 在个股标题区或分类卡中展示：

- 基本面板块；
- 大盘板块 ETF；
- 主题组及主题 ETF；
- 主模型分组；
- 来源与置信度；
- 基本面和行情分类冲突；
- 待复核原因。

市场与板块页面的成员数、热力图、板块风险和个股预测必须使用同一个分组快照。点击板块仍可查看全部成员；点击主题可查看主题成员，但不得把 `ai_infrastructure` 标成半导体成分。

顶部派发和其他分组风险模型在 UI 输出中显示所用上下文，例如：

```text
市场：QQQ
大板块：科技 / XLK
主题：半导体 / SOXX+SMH
个股：SNDK
```

## 点时性与缓存

- 分类查询必须带 `asof`，历史预测使用当时已经生效的分组；
- 行情分类只能使用观察日之前的数据；
- 人工覆盖按 `effective_from` 和可选 `effective_to` 生效；
- 分组快照的指纹进入预测 artifact identity；
- 分类或覆盖变化后必须使相关预测缓存失效；
- 不允许用当前分类覆盖退市股票或历史时期的旧分类而不留下版本。

## 迁移步骤

1. 建立统一注册表、覆盖格式和 `GroupAssignment` 解析器。
2. 为现有研究库生成分组覆盖率审计；将 SNDK 加入半导体主题。
3. 将新股票导入流程接入强制分类和审计。
4. 将预测风险上下文从硬编码成员迁移到动态成员快照。
5. 让股票池、个股页和市场板块页读取同一分组契约。
6. 重建预测缓存并验证 SNDK 历史顶部风险事件。
7. 删除已经无消费者的硬编码成员名单。

迁移期间保留兼容适配器，但新旧分组结果不一致时测试必须失败，不允许长期双轨。

## 测试与验收

### 单元测试

- 11 个板块与 ETF 一一映射；
- SIC 精确、范围、未知代码分类；
- 主题规则和人工覆盖优先级；
- 覆盖生效日期和冲突检测；
- SNDK 解析为科技板块、半导体主题和半导体主模型组；
- 无法分类的新股票进入 `unclassified_review`。

### 集成测试

- 所有当前研究池股票都有分组记录；
- 新导入股票自动获得分组；
- API、市场页和预测服务返回相同分组；
- 分组 revision 改变会使预测缓存失效；
- 历史日期只使用当时有效的分类。

### 模型回归

- SNDK 顶部风险时间线可用；
- SNDK 在 2026-06-16 和 2026-06-26 进入观察状态；
- SNDK 在 2026-07-02 进入确认状态；
- SNDK 在 2026-06-29 至 2026-07-01 保留风险记忆；
- 已有 MU、NBIS、ADBE 等模型结果不因动态分组迁移而无故消失；
- AI 基础设施相关股不会被 UI 错标为半导体成分。

### 全量验收

- 当前研究库活跃普通股分组覆盖率为 100%；
- 标准板块、主题和 ETF 引用均通过完整性检查；
- `unclassified_review` 数量和名单显式输出；
- Python、JavaScript、API 和浏览器测试全部通过；
- 本地服务重启后，SNDK 图表显示顶部风险事件和分组说明。

## 非目标

- 不根据预测涨跌结果反向调整分类；
- 不把交易行为分类描述为公司的真实业务板块；
- 不在本阶段引入付费行业分类数据；
- 不把证据评分描述为概率；
- 不因为分类覆盖率目标而强行猜测未知股票板块。
