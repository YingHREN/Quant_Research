# 退市股票历史日线暂存研究库设计

## 目标

把已经完成审计的 13,039 只退市普通股候选及其 2016-01-01 至
2026-07-27 原始 EODHD 响应导入独立 SQLite 暂存库。数据库只承担研究数据
清洗、身份审计和后续点时分类的输入职责，不覆盖 `data/prices.db` 或
`data/research_prices.db`，也不把交易首末日期解释为指数或板块成员区间。

输入已经冻结为：

- 13,039 只候选；
- 6,779 只含可用历史，6,239 只为空响应；
- 5,917,822 条供应商原始行；
- 5,913,990 条有效日线和 3,832 条非法行；
- 目录 SHA-256
  `fa432000e8f77577175d2b13590dd0be65d44fef5ceceed433f3d33752ad80b0`。

## 方案选择

采用独立 `data/delisted_research_prices.db`，而不是写入现有研究库或改用
Parquet。独立库能隔离代码复用、身份冲突和退市状态错误；同时沿用现有
SQLite 研究工具和 `research_prices_v1` 的价格字段，使后续跨库实验无需新增
存储依赖。

现有 `ResearchPriceStore.import_security()` 不直接用于本任务：它会在任意
非法行出现时整票失败，并默认把证券标为活跃。新导入器会复用经过验证的价格
字段语义，但使用专门的退市审计契约。

## 数据库边界

数据库由临时文件完整构建，全部验收通过后原子替换目标文件。构建失败或中断
时删除临时文件并保留已有正式文件。

核心表：

### `security_master`

每个冻结候选恰好一行：

- `ticker`、`name`、`exchange`、`currency`；
- `provider_isin`、`identity_status`、`identity_key`；
- `classification` 固定为 `accepted_common`；
- `active` 固定为 0，`is_delisted` 固定为 1；
- `provider` 固定为 `eodhd`；
- `catalog_sha256`、`snapshot_date`、`imported_at`。

当前冻结目录不存在跨交易所重复 ticker。导入前仍必须验证 ticker 唯一，禁止
静默改名或自动拼接相同代码。

### `daily_prices`

仅保存逐行验证通过的行情：

- 原始 OHLC；
- 供应商 `adjusted_close`；
- 按 `adjusted_close / raw_close` 生成的复权 OHLC；
- 成交量、调整因子、历史段编号；
- provider、snapshot、imported_at 和调整方法版本。

主键为 `(ticker, date)`。超过 180 个自然日的间隔生成新的
`history_segments`，但段边界只是代码/交易连续性提示，不自动合并身份。

### `history_segments`

保存每只证券的分段首日、末日、行数、前置缺口天数以及最后一段标识。空响应
证券没有 segment。

### `security_audits`

13,039 只候选必须各有一行，保存：

- `request_status`、`quality_status`；
- 原始行、有效行、非法行和重复日期数；
- 首末有效交易日、原始字节数；
- 冻结目录版本、回填版本和目录哈希；
- 原始响应相对路径。

空数组是已完成的 `empty` 状态，不是缺失或待重试。

### `rejected_daily_rows`

保存 3,832 条被拒行情的 ticker、原始序号、可枚举拒绝原因和原始 JSON。
拒绝原因至少区分日期非法、重复日期、数值非法、非正价格、OHLC 结构非法和
负成交量。该表用于审计，不参与任何收益或因子计算。

### `import_runs`

每次成功构建保存 schema/import 版本、目录哈希、输入/输出计数、数据库字节
数、开始/完成时间和完整性状态。正式库只包含最后一次成功的完整构建。

## 数据流

1. 读取 `candidates.json`、正式 CSV/JSON 报告和 13,039 个响应文件。
2. 验证 schema、版本、目录哈希、窗口和候选一一对应。
3. 对每个响应逐行分流为有效行与拒绝行；不直接信任 CSV 汇总。
4. 将有效行标准化并生成历史段；空响应只写证券和审计。
5. 每只证券使用一个事务，避免半只证券写入；整个目标使用临时数据库隔离。
6. 汇总数据库实际计数，并与正式回填报告逐项比对。
7. 执行 `PRAGMA foreign_key_check`、`PRAGMA integrity_check` 和关键唯一性检查。
8. 所有门槛通过后关闭连接并原子替换正式暂存库。

## 守恒与失败策略

构建必须满足：

- `security_master = security_audits = 13,039`；
- `daily_prices = 5,913,990`；
- `rejected_daily_rows = 3,832`；
- `daily_prices + rejected_daily_rows = 5,917,822`；
- 空响应 = 6,239；
- 所有历史文件均为 JSON 数组；
- 没有候选遗漏、额外 ticker、重复 `(ticker,date)` 或未知拒绝原因；
- 报告目录哈希与冻结候选、manifest 和导入运行一致。

任何不守恒、损坏 JSON、目录变化或 SQLite 检查失败都会终止构建，不发布
部分数据库。错误消息只包含证券和错误类型，不包含 API token。

## 接口与产物

新增纯函数模块负责逐行分流和汇总契约；CLI 负责文件系统和 SQLite：

- `research/delisted_history_staging.py`
- `build_delisted_research_db.py`
- `data/delisted_research_prices.db`（Git 忽略）
- `reports/delisted-history-staging-import.json`
- `reports/delisted-history-staging-import.md`

CLI 必须接受显式输入和输出路径，便于测试使用临时目录。真实运行不会读取或
修改现有两个价格数据库。

## 测试与验收

自动测试覆盖：

- 有效、空、非法和重复行情的逐行分流；
- 身份、退市状态和来源字段；
- 历史分段；
- 空响应审计；
- 幂等完整重建；
- 损坏输入、目录哈希不一致和计数不守恒时拒绝发布；
- 临时文件清理和已有正式库保留；
- 外键、唯一性和 SQLite 完整性；
- 报告无凭据和 JSON 可复现。

真实导入后记录数据库实际行数、证券数、拒绝原因分布、体积和运行时间。完成
本阶段只关闭“退市日线暂存导入”，历史行业分类和点时成员区间继续保留在全局
TODO 中。
