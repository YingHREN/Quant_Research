# 历史点时指数股票池 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 EODHD S&P 500 历史成分和美国代码变更标准化、可审计地写入研究库，并按任意观察日期读取真实有效成员。

**Architecture:** `data/point_in_time_universe.py` 只负责外部 JSON 的严格标准化；`ResearchPriceStore` 负责事务与迁移；`ExpandedMarketDataRepository` 负责只读点时查询；采集器与导入器保持分离。缺失历史返回空结果，不回退到当前分类。

**Tech Stack:** Python 3.9、SQLite、urllib、JSON、unittest。

## Global Constraints

- 成员区间固定使用 `[effective_from, effective_to)`。
- 原始 API token 只从 `EODHD_API_TOKEN` 读取。
- 空响应、非法日期和冲突重复不得替换已有数据。
- 旧代码和新代码在无可靠身份键时不得合并价格。
- 本轮不修改线上预测策略或 UI。

---

### Task 1: 历史成分与代码变更标准化

**Files:**
- Create: `data/point_in_time_universe.py`
- Create: `tests/test_point_in_time_universe.py`

**Interfaces:**
- Produces: `normalize_historical_components(payload) -> tuple[HistoricalMembership, ...]`
- Produces: `normalize_symbol_changes(payload) -> tuple[SymbolChange, ...]`

- [x] 写失败测试，使用手工官方形态样例验证代码大写、半开区间字段、退市标志和排序。
- [x] 写失败测试，验证嵌套 `HistoricalTickerComponents`、空响应、非法日期、结束日不晚于开始日和冲突重复。
- [x] 运行 `python -m unittest tests.test_point_in_time_universe -v`，确认接口缺失失败。
- [x] 实现不可变数据类和严格标准化；完全相同的重复行去重，冲突重复抛出 `ValueError`。
- [x] 重跑目标测试，确认通过。
- [x] 提交 `data: normalize point-in-time universe feeds`。

### Task 2: 事务存储与兼容迁移

**Files:**
- Modify: `data/research_store.py`
- Modify: `tests/test_research_store.py`

**Interfaces:**
- Consumes: Task 1 的标准化记录。
- Produces: `ResearchPriceStore.replace_universe_memberships(...) -> int`
- Produces: `ResearchPriceStore.upsert_symbol_changes(...) -> int`

- [x] 写失败测试：已有旧版表可增加审计列且保留数据。
- [x] 写失败测试：整体替换幂等、空记录拒绝、失败事务保留旧成员。
- [x] 写失败测试：退市目录记录进入 `security_master`，代码变更幂等但不改写价格。
- [x] 运行目标测试并确认缺少存储接口。
- [x] 实现兼容列迁移、新代码变更表、最小证券目录 upsert 和事务写入。
- [x] 重跑研究存储与标准化测试。
- [x] 提交 `data: store historical universe intervals`。

### Task 3: 点时读取

**Files:**
- Modify: `research/expanded_market_data.py`
- Modify: `tests/test_expanded_market_data.py`

**Interfaces:**
- Produces: `load_universe_members(*, universe_key, asof) -> dict`
- Produces: `load_universe_members_by_date(*, universe_key, observation_dates) -> dict[str, frozenset[str]]`

- [x] 写失败测试：加入日包含、移出日排除、退市股票在更早日期仍存在。
- [x] 写失败测试：必须传 `asof`、未知股票池为空、批量日期与单日结果一致。
- [x] 运行目标测试并确认读取接口缺失。
- [x] 实现单次 SQL 区间读取和批量内存展开。
- [x] 重跑读取、存储和标准化测试。
- [x] 提交 `research: read point-in-time universe members`。

### Task 4: 采集、导入与覆盖审计

**Files:**
- Create: `collect_eodhd_point_in_time_universe.py`
- Create: `import_point_in_time_universe.py`
- Create: `tests/test_point_in_time_universe_cli.py`

**Interfaces:**
- 原始目录包含 `historical_components.json`、`symbol_changes.json` 和 `manifest.json`。
- 导入器输出成员、退市成员、代码变更和区间计数。

- [x] 写失败测试：离线输入模式原子写三份 JSON，manifest 不含 token。
- [x] 写失败测试：导入临时 SQLite 后按 2018/2020/当前日期生成覆盖审计。
- [x] 实现带重试的 EODHD 采集和 `--input-components/--input-symbol-changes` 离线模式。
- [x] 实现导入器与 JSON/Markdown 覆盖报告。
- [x] 重跑 CLI 与全部相关测试。
- [x] 尝试真实 EODHD 快照；若权限/网络不可用，保留明确阻塞状态而不伪造结果。
- [x] 提交 `data: add point-in-time universe pipeline`。

### Task 5: 总体验证与合入

**Files:**
- Modify: `docs/modeling-todo.md`
- Create when data is available: `reports/point-in-time-universe.json`
- Create when data is available: `reports/point-in-time-universe.md`

- [x] 更新中文 TODO，只关闭已获得真实证据的历史成员子项。
- [x] 运行 `LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error venv/bin/python -m unittest discover -s tests -q`。
- [x] 运行 `git diff --check` 并核对无 token、数据库 WAL/SHM 或临时文件进入提交。
- [x] 合入 main，合并后再次运行完整测试。
