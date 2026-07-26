# 高流动性股票池与板块目录设计

## 目标

在不污染现有 194 只看板股票池的前提下，建立可复现的美国高流动性研究股票池，并为每只股票保存基本板块、行业、主题和分类来源。目录先服务回测与数据审计；生产价格库和 UI 接入作为后续独立阶段。

## 股票池

第一版 `liquid_us_common_v1` 使用 2026-07-24 EODHD 批量快照和活跃证券清单，条件固定为：

- 证券类型为 `Common Stock`；
- 交易所为 NASDAQ、NYSE、NYSE MKT、NYSE ARCA 或 BATS；
- 最新复权收盘价至少 5 美元；
- 市值至少 3 亿美元；
- 50 日平均成交额至少 1 亿美元；
- ticker 必须符合本项目安全符号格式。

候选目录记录观察日、门槛、名称、交易所、ISIN、市值、价格、50 日均量、50 日平均成交额和是否已存在于本地库。下载历史不足 60 个交易日的证券保留在候选目录，但不进入第一版可训练目录。

## 分类

`sec_sic_v1` 使用 SEC submissions 的 SIC 代码和行业描述。输出基本板块采用与现有板块 ETF 一致的 11 类键：

`technology`、`communication`、`consumer_discretionary`、`consumer_staples`、`energy`、`financials`、`health_care`、`industrials`、`materials`、`real_estate`、`utilities`。

不能可靠映射的代码输出 `unclassified`，不得静默归入科技或工业。分类记录 SIC、SEC 行业描述、规则版本、来源、观察日和置信度。精确四位代码例外的置信度高于宽泛的两位行业段映射。

现有 `semiconductor`、`software` 和 AI 基础设施人工主题继续独立保存，不覆盖基本板块。以后增加基于板块 ETF 相关性的 `market_behavior_v1`，作为交易行为分类，不改写 SEC 基本分类。

## 数据质量与边界

- EODHD 原始日线、拆股和分红继续保存在 Git 忽略缓存。
- SEC 请求遵守每秒不超过 10 次的公平访问限制，并使用明确 User-Agent。
- 原始响应不因分类失败而删除。
- 超过 180 日的交易断点需要切段审计；NBIS 的 969 日断点保持显式。
- 当前阶段只生成研究目录，不向 `prices` 表批量写入，不改变看板默认股票池。
- 下一阶段导入价格时，现有 Tiingo 行不被 EODHD 无条件覆盖；新增股票使用经过拆股/分红校验的复权 OHLCV。

## 验收

- 1,015 个候选全部拥有 SEC CIK 和 SIC 身份数据。
- 通过历史门槛的证券生成唯一目录行。
- 每行都有基本板块或显式 `unclassified`、来源、规则版本和置信度。
- 相同输入重复运行产生字节稳定的排序和内容。
- 测试覆盖关键 SIC 例外、宽泛行业段、未知代码、重复 ticker 和短历史过滤。
