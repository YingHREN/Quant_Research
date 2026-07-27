# 退市普通股历史日线分层试验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定抽取 250 只美国主交易所退市普通股，真实测量 EODHD 日线可用性、质量和全量回填规模。

**Architecture:** `research/delisted_history_pilot.py` 保持纯函数，负责目录过滤、确定性分层抽样、单股质量审计和规模汇总；运行器只负责网络、缓存和报告。原始数据不入研究数据库。

**Tech Stack:** Python 3.9、urllib、JSON、CSV、unittest。

## Global Constraints

- 样本在请求日线前冻结，失败不得替换。
- NASDAQ/NYSE/NYSE MKT 样本量固定为 100/100/50。
- 查询窗口固定为 2016-01-01 至 2026-07-27。
- token 只从环境变量读取。
- 不写生产数据库，不把交易区间称为指数成员区间。

---

### Task 1: 固定分层抽样

**Files:**
- Create: `research/delisted_history_pilot.py`
- Create: `tests/test_delisted_history_pilot.py`

**Interfaces:**
- Produces: `select_stratified_sample(catalog, quotas) -> tuple[dict, ...]`

- [ ] 写失败测试，验证交易所/币种/类型/代码过滤和 100/100/50 配额。
- [ ] 写失败测试，验证输入顺序变化不改变样本，配额不足明确失败。
- [ ] 运行目标测试并确认接口缺失。
- [ ] 实现固定 SHA-256 排序与样本行标准化。
- [ ] 重跑测试并提交 `research: freeze delisted history sample`。

### Task 2: 日线质量审计与规模估计

**Files:**
- Modify: `research/delisted_history_pilot.py`
- Modify: `tests/test_delisted_history_pilot.py`

**Interfaces:**
- Produces: `audit_history_rows(sample_row, payload, raw_bytes) -> dict`
- Produces: `summarize_pilot(sample, audits, catalog) -> dict`

- [ ] 写失败测试，覆盖合法行、重复日期、非法 OHLC、空响应和 2018 后相关性。
- [ ] 写失败测试，以手算小样本验证成功率、平均/P90 字节和交易所外推。
- [ ] 运行目标测试并确认接口缺失。
- [ ] 实现逐行质量审计与固定统计口径。
- [ ] 重跑测试并提交 `research: audit delisted history coverage`。

### Task 3: 可续跑采集器与报告

**Files:**
- Create: `run_delisted_history_pilot.py`
- Create: `tests/test_run_delisted_history_pilot.py`

**Interfaces:**
- 原始目录包含 `catalog.json`、`sample.json`、`histories/*.json`、
  `errors.json` 和 `manifest.json`。

- [ ] 写失败测试，离线 fixture 运行后生成 CSV/JSON/Markdown 且不含 token。
- [ ] 写失败测试，已有合法历史文件被复用，错误代码不触发替换抽样。
- [ ] 实现原子缓存、有限重试、并发采集和报告渲染。
- [ ] 重跑相关测试并提交 `research: add delisted history pilot runner`。

### Task 4: 真实试验与合入

**Files:**
- Create: `reports/delisted-history-pilot.csv`
- Create: `reports/delisted-history-pilot.json`
- Create: `reports/delisted-history-pilot.md`
- Modify: `docs/modeling-todo.md`

- [ ] 使用真实 32,371 条目录运行固定 250 只试验。
- [ ] 检查样本未替换、报告无 token、原始缓存未进入 Git。
- [ ] 更新中文 TODO，记录真实成功率和全量规模估计。
- [ ] 运行全量测试与 `git diff --check`。
- [ ] 合入 main，合并后再次运行全量测试。
