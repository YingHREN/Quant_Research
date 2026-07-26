# 高流动性股票池与板块目录实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从已采集的 EODHD 与 SEC 原始数据生成可复现、带来源和置信度的高流动性股票池及板块目录。

**Architecture:** 纯函数模块负责 SIC 到基本板块的版本化映射；独立目录构建器读取忽略缓存、验证历史覆盖并生成确定性 JSON。当前生产价格库和 UI 保持不变。

**Tech Stack:** Python 3、标准库 JSON/pathlib、unittest。

## Global Constraints

- 股票池规则固定为 `liquid_us_common_v1`。
- 基本分类规则固定为 `sec_sic_v1`。
- 未知 SIC 必须输出 `unclassified`。
- 不修改或批量写入现有 `prices` 表。
- 生成数据位于 `data/cache/`，不提交 Git。

---

### Task 1: 版本化 SIC 板块分类器

**Files:**
- Create: `data/sector_classification.py`
- Create: `tests/test_sector_classification.py`

**Interfaces:**
- Produces: `classify_sic(sic, description="") -> SectorClassification`
- Produces: `SectorClassification.to_dict() -> dict`

- [ ] 写失败测试，覆盖半导体、软件、制药、银行、REIT、能源、公用事业、未知 SIC。
- [ ] 运行测试并确认因模块不存在而失败。
- [ ] 实现精确四位例外和两位行业段映射，未知值显式返回 `unclassified`。
- [ ] 运行分类器测试及现有 market group 测试。

### Task 2: 确定性研究目录构建器

**Files:**
- Create: `data/universe_catalog.py`
- Create: `tests/test_universe_catalog.py`

**Interfaces:**
- Consumes: `classify_sic`
- Produces: `build_catalog(universe_payload, identities, history_lengths, asof) -> dict`
- Produces: `write_catalog(source_root, sec_root, output_path) -> dict`

- [ ] 写失败测试，证明短历史被排除、重复 ticker 被拒绝、输出按 ticker 排序且保留来源字段。
- [ ] 运行测试并确认因接口不存在而失败。
- [ ] 实现输入验证、SEC 身份合并、分类和确定性 JSON 输出。
- [ ] 运行单元测试并对真实缓存生成目录。

### Task 3: 数据审计与 TODO 更新

**Files:**
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: 生成的目录审计统计。
- Produces: DATA-001 的真实扩池进度记录。

- [ ] 审计候选数、可训练数、板块分布、未知分类、历史行数和缓存规模。
- [ ] 更新中文全局 TODO，区分“原始采集完成”和“生产导入/UI 尚未完成”。
- [ ] 运行相关单元测试和数据库完整性检查。
- [ ] 检查 Git diff，确认凭据和生成数据没有进入版本控制。
