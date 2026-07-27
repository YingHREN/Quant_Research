# 退市股票历史日线暂存研究库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 13,039 只冻结退市候选的 5,913,990 条有效日线与 3,832 条拒绝行导入独立、原子发布且可审计的 SQLite 暂存研究库。

**Architecture:** 纯函数模块逐行复现正式回填审计规则并返回有效/拒绝分区；CLI 校验冻结候选、报告和缓存守恒，在临时 SQLite 中逐证券写入价格、分段、身份和审计，完整性检查通过后原子替换目标库。现有 `prices.db` 和 `research_prices.db` 始终只读且不参与构建。

**Tech Stack:** Python 3.9、SQLite、JSON、CSV、unittest。

## Global Constraints

- schema 版本固定为 `delisted_research_prices_v1`。
- 导入版本固定为 `delisted_history_staging_import_v1`。
- 输入目录 SHA-256 固定来自冻结候选，不根据交易日期推断历史成员关系。
- 目标固定为独立数据库；不得覆盖或附加写入现有两个价格数据库。
- 任何守恒或 SQLite 检查失败都不得发布 `.tmp`。
- API token 不进入数据库、报告、日志或异常消息。

---

### Task 1: 逐行有效/拒绝分区

**Files:**
- Create: `research/delisted_history_staging.py`
- Create: `tests/test_delisted_history_staging.py`

**Interfaces:**
- Produces: `partition_history_rows(payload: list) -> tuple[list, list]`
- Produces: `summarize_partitions(partitions: Sequence[Mapping]) -> dict`

- [ ] 写失败测试：有效行原样保留；非法日期、重复日期、非有限数值、非正
  价格、OHLC 结构错误和负成交量分别生成稳定 reason；重复日期判定顺序与
  `audit_history_rows()` 一致。
- [ ] 运行 `python -m unittest tests.test_delisted_history_staging -q`，确认因
  模块缺失失败。
- [ ] 实现 `RejectedDailyRow(source_index, reason, raw_json)` 与严格分区；
  `raw_json` 使用稳定排序且不得修改供应商行。
- [ ] 增加手算汇总测试，验证 raw/valid/rejected/reason 数量守恒。
- [ ] 运行目标测试和 `tests.test_delisted_history_pilot`，确认两套审计数量
  一致且无 warning。
- [ ] 提交 `data: partition delisted history rows for staging`。

### Task 2: 原子 SQLite 暂存库构建器

**Files:**
- Create: `build_delisted_research_db.py`
- Create: `tests/test_build_delisted_research_db.py`

**Interfaces:**
- Consumes: `partition_history_rows()`、`normalize_daily_rows()`
- Produces: `build_database(candidates_path, audit_csv_path, raw_root, output_path, report_json, report_markdown, imported_at=None) -> dict`

- [ ] 写失败测试：一个有效证券、一个空响应、一个含非法行证券均写入
  `security_master` 和 `security_audits`；只有有效行进入 `daily_prices`，
  非法行进入 `rejected_daily_rows`，所有证券 `active=0/is_delisted=1`。
- [ ] 写失败测试：超过 180 日缺口生成多个 `history_segments`；identity
  status/key、exchange、catalog hash 和原始相对路径完整保存。
- [ ] 写失败测试：重复 ticker、损坏 JSON、候选/CSV 不守恒、逐票计数与 CSV
  不一致、目录哈希变化和未知响应状态均拒绝发布。
- [ ] 写失败测试：已有目标库存在时，构建中途失败保留旧文件并删除 `.tmp`；
  相同输入重复构建得到相同数据计数。
- [ ] 运行 `python -m unittest tests.test_build_delisted_research_db -q`，确认
  缺少构建接口。
- [ ] 实现六张表：`security_master`、`daily_prices`、`history_segments`、
  `security_audits`、`rejected_daily_rows`、`import_runs`；启用外键并为
  `(date)`、`(ticker,segment_id,date)` 和审计状态建索引。
- [ ] 实现逐证券事务、批量插入、报告生成、`foreign_key_check`、
  `integrity_check`、守恒核对和原子替换；报告只含汇总，不嵌入 13,039 行。
- [ ] 增加 CLI 参数及默认真实路径，确保不导入 splits/dividends、不创建
  universe membership 或 sector classification。
- [ ] 运行 Task 1/2、research store 和正式回填相关测试。
- [ ] 提交 `data: add delisted research staging database builder`。

### Task 3: 验证、合入与真实导入

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/plans/2026-07-27-delisted-history-staging-database.md`
- Create after import: `reports/delisted-history-staging-import.json`
- Create after import: `reports/delisted-history-staging-import.md`

- [x] 在隔离工作树运行全量测试、`git diff --check` 和新增差异密钥扫描。
- [x] 本地非快进合入 `main`，并在合并结果上再次运行全量测试。
- [x] 从 `main` 构建 `data/delisted_research_prices.db`；记录运行时间、数据库
  字节数、证券/有效行/拒绝行/空响应/segment 数和拒绝原因分布。
- [x] 使用只读连接验证 `13,039 / 5,913,990 / 3,832 / 6,239` 守恒、
  `foreign_key_check` 为空、`integrity_check=ok`，并确认现有两个价格库的
  构建前后 SHA-256 不变。
- [x] 验证目标数据库和 `.tmp` 的 Git 忽略状态、报告可跟踪、无 token、
  无残留临时文件。
- [x] 更新中文 TODO，只关闭退市日线暂存导入；继续保留历史行业分类、点时
  成员区间和跨库模型实验。
- [x] 提交真实报告和 TODO，运行最终 768+ 项全量测试并记录完成。
