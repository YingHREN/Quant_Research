# 退市普通股历史日线正式回填 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 13,039 个净化后的退市普通股候选提供可续跑、可审计且不写生产库的十年 EODHD 日线回填。

**Architecture:** 纯函数模块负责冻结候选、校验审计守恒和生成汇总；运行器负责网络、并发、原子缓存、checkpoint 和报告。实现先合入 main，再从 main 启动真实长时回填，避免工作树清理丢失大体积缓存。

**Tech Stack:** Python 3.9、urllib、ThreadPoolExecutor、JSON、CSV、unittest。

## Global Constraints

- 回填版本固定为 `delisted_history_backfill_v1`。
- 查询窗口固定为 2016-01-01 至 2026-07-27。
- 候选在任何网络请求前冻结，失败和空响应不得替换。
- 原始 JSON 不写 `research_prices.db`，不自动拼接代码或冲突 ISIN。
- token 只从环境变量或测试注入读取，不持久化。

---

### Task 1: 候选冻结与审计汇总

**Files:**
- Create: `research/delisted_history_backfill.py`
- Create: `tests/test_delisted_history_backfill.py`

**Interfaces:**
- Produces: `freeze_candidates(catalog, catalog_sha256, start, finish) -> dict`
- Produces: `summarize_backfill(candidates, audits) -> dict`

- [ ] 写失败测试：只选择 accepted/eligible，按交易所和代码排序，拒绝重复、
  schema/rule/哈希不一致，并以手算样本验证各状态、交易所、行数和字节汇总。
- [ ] 运行目标测试，确认因模块或接口缺失而失败。
- [ ] 实现 `candidates.json` 契约和严格审计守恒；错误/空响应也必须与冻结候选
  一一对应。
- [ ] 重跑目标测试，确认无 warning。
- [ ] 提交 `data: freeze delisted history backfill candidates`。

### Task 2: 可续跑采集器与报告

**Files:**
- Create: `run_delisted_history_backfill.py`
- Create: `tests/test_run_delisted_history_backfill.py`

**Interfaces:**
- Consumes: Task 1 纯函数和 `audit_history_rows`
- Produces: `run_backfill(catalog_path, catalog_report_path, raw_root, report_csv, report_json, report_markdown, token=None, fetcher=None, workers=8, checkpoint_every=100, updated_at=None) -> dict`

- [ ] 写失败测试：第一次运行在 fetch 前已存在冻结清单；成功、空、404 和
  retryable error 分别落盘并进入正确状态；第二次只重试 retryable error。
- [ ] 写失败测试：损坏缓存重下、目录哈希或窗口变化拒绝、checkpoint/report
  无 token 与 `.tmp`，CSV 含全部候选审计。
- [ ] 运行测试，确认缺少运行器接口。
- [ ] 实现 EODHD fetch、4 次有限重试、8 并发、100 项 checkpoint、永久错误
  复用、可重试错误续跑及 partial/complete 汇总。
- [ ] 重跑 Task 1/2、试点和净化相关测试。
- [ ] 提交 `data: add resumable delisted history backfill`。

### Task 3: 验证、合入与真实回填

**Files:**
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/plans/2026-07-27-delisted-history-backfill.md`
- Create after real run: `reports/delisted-history-backfill.csv`
- Create after real run: `reports/delisted-history-backfill.json`
- Create after real run: `reports/delisted-history-backfill.md`

- [ ] 在工作树运行全量测试、`git diff --check` 和密钥扫描。
- [ ] 非快进合入 main，并在 main 再次运行全量测试。
- [ ] 从 main 使用真实净化目录启动 13,039 只回填；每次进度更新核对处理数、
  下载数、空响应和错误数，网络中断时使用同一命令续跑。
- [ ] 验证候选守恒、缓存哈希、质量统计、无密钥/临时文件和 Git 忽略状态。
- [ ] 更新中文 TODO：记录真实可用历史、有效行数、体积和失败；只关闭正式
  原始回填，保留暂存库导入、历史行业与点时成员任务。
- [ ] 提交真实报告，运行最终全量测试并记录完成。
