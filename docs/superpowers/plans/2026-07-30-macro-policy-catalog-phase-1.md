# 点时政策事件目录第一阶段实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可迁移、可点时重放、可审计的联储政策事件目录，并产出不带预测权限的政策时期描述结果。

**Architecture:** 在现有独立宏观 SQLite 数据库中增加政策事件与人工时期标签两张表；官方事件、人工标签和机制解释分别保存。研究模块只通过 `available_at <= asof` 的只读接口消费事件，再结合本地复权 ETF 日线计算完整时期的描述统计；进行中时期、未上市 ETF 和数据缺口保持缺失。

**Tech Stack:** Python 3.9、SQLite、pandas、`unittest`、现有 `MacroObservationStore` 与本地日线数据库。

## Global Constraints

- 模型任务编号固定为 `MACRO-ROTATION-001`。
- 第一版 `lifecycle=research`、`decision_permission=advisory`、`online_authority=none`。
- 官方事件、人工时期标签和机制解释必须分开保存。
- 历史读取只允许 `available_at <= asof`；未来修订不得改变旧时点结果。
- 未发布值、尚未上市 ETF、进行中时期和数据缺口必须保持缺失，不得代理回填。
- 第一阶段不修改 Ridge 原始值、不进入向下否决、不输出板块买卖建议。
- 标准 ETF 集合固定为 `SPY QQQ XLK XLC XLY XLP XLE XLF XLV XLI XLB XLRE XLU`。

---

### Task 1: 扩展宏观存储的数据修订契约

**Files:**
- Modify: `web/services/macro_store.py`
- Modify: `fetch_macro_data.py`
- Test: `tests/test_web_macro_store.py`
- Modify: `tests/test_fetch_macro_data.py`

**Interfaces:**
- Consumes: `MacroObservationStore.initialize()`, `MacroObservationStore.upsert(rows)`
- Produces: `macro_observations.revision_policy TEXT NOT NULL`；旧数据库自动迁移；`load_available()` 返回 `revision_policy`

- [x] **Step 1: 写失败测试，覆盖新库、旧库迁移和缺失字段拒绝**

```python
class MacroObservationRevisionPolicyTest(unittest.TestCase):
    def test_initialize_migrates_legacy_table_without_losing_rows(self):
        # 先手工建立旧表并插入一行，再调用 initialize。
        # 断言原行仍存在，revision_policy 为 legacy_unspecified。

    def test_upsert_requires_revision_policy(self):
        # 删除输入中的 revision_policy，断言 ValueError。

    def test_load_available_returns_revision_policy(self):
        # 写入 initial_release_only，断言读取结果保留该值。
```

- [x] **Step 2: 运行测试并确认因缺少列/校验而失败**

Run: `./venv/bin/python -m unittest tests.test_web_macro_store`

Expected: FAIL，错误指向 `revision_policy` 尚未实现。

- [x] **Step 3: 实现幂等迁移和严格行校验**

```python
REVISION_POLICY_LEGACY = "legacy_unspecified"

def _ensure_revision_policy_column(connection):
    columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(macro_observations)")
    }
    if "revision_policy" not in columns:
        connection.execute(
            "ALTER TABLE macro_observations "
            "ADD COLUMN revision_policy TEXT NOT NULL "
            "DEFAULT 'legacy_unspecified'"
        )
```

更新建表、插入、冲突更新、查询和 `_normalized_row()`，不改变现有主键。

- [x] **Step 4: 让 ALFRED 初次发布导入显式写入修订策略**

```python
row["revision_policy"] = "initial_release_only"
```

不得从日志或报告输出 API key。

- [x] **Step 5: 运行相关回归**

Run: `./venv/bin/python -m unittest tests.test_web_macro_store tests.test_fetch_macro_data tests.test_web_macro_risk_service tests.test_macro_risk`

Expected: PASS。

- [x] **Step 6: 提交**

```bash
git add web/services/macro_store.py fetch_macro_data.py \
  tests/test_web_macro_store.py tests/test_fetch_macro_data.py
git commit -m "feat: preserve macro revision policies"
```

### Task 2: 建立政策事件与人工时期的版本化存储

**Files:**
- Create: `web/services/policy_event_store.py`
- Create: `tests/test_policy_event_store.py`

**Interfaces:**
- Produces: `PolicyEventStore.initialize()`
- Produces: `PolicyEventStore.upsert_events(rows) -> int`
- Produces: `PolicyEventStore.upsert_periods(rows) -> int`
- Produces: `PolicyEventStore.load_events(asof, event_types=()) -> pandas.DataFrame`
- Produces: `PolicyEventStore.load_periods(asof, include_incomplete=True) -> pandas.DataFrame`

- [x] **Step 1: 写失败测试，冻结事件与时期字段**

```python
EVENT = {
    "event_id": "fomc-2026-07-rate",
    "event_type": "policy_rate",
    "effective_date": "2026-07-30",
    "available_at": "2026-07-29T18:00:00+00:00",
    "source_url": "https://www.federalreserve.gov/",
    "source_title": "Federal Reserve official release",
    "source_published_at": "2026-07-29T18:00:00+00:00",
    "payload_json": '{"target_upper":4.5,"target_lower":4.25}',
    "catalog_version": "fed-policy-v1",
}
```

测试同一 `event_id + catalog_version` 幂等、晚于 `asof` 的事件不可见、无 UTC
偏移拒绝、非官方来源域拒绝、人工时期可独立修改而不改写官方事件。

- [x] **Step 2: 运行测试并确认模块不存在**

Run: `./venv/bin/python -m unittest tests.test_policy_event_store`

Expected: FAIL with `ModuleNotFoundError`。

- [x] **Step 3: 实现两张相互独立的表**

```sql
CREATE TABLE policy_events (
    event_id TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    available_at TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_title TEXT NOT NULL,
    source_published_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (event_id, catalog_version)
);

CREATE TABLE policy_periods (
    period_id TEXT NOT NULL,
    catalog_version TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    label_en TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    available_at TEXT NOT NULL,
    interpretation_zh TEXT NOT NULL,
    interpretation_en TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    PRIMARY KEY (period_id, catalog_version)
);
```

所有 JSON 入库前按 key 排序并验证能解码；加载使用 SQLite 只读 URI。

- [x] **Step 4: 运行单元测试**

Run: `./venv/bin/python -m unittest tests.test_policy_event_store`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add web/services/policy_event_store.py tests/test_policy_event_store.py
git commit -m "feat: add point-in-time policy event store"
```

### Task 3: 增加受控目录导入器

**Files:**
- Create: `data/fed_policy_catalog_v1.json`
- Create: `import_policy_catalog.py`
- Create: `tests/test_import_policy_catalog.py`

**Interfaces:**
- Consumes: `PolicyEventStore.upsert_events`, `PolicyEventStore.upsert_periods`
- Produces: `import_catalog(path, database) -> {"events": int, "periods": int, "catalog_version": str}`

- [x] **Step 1: 写失败测试，确保导入原子性和秘密安全**

```python
def test_import_rejects_period_with_unknown_source_event(self):
    # period 引用不存在的 event_id，断言整个导入不写入任何行。

def test_import_is_idempotent_and_summary_has_no_database_path(self):
    # 连续导入两次，断言行数不重复，返回值不含绝对路径或凭据形状。
```

- [x] **Step 2: 运行测试并确认导入器不存在**

Run: `./venv/bin/python -m unittest tests.test_import_policy_catalog`

Expected: FAIL with `ModuleNotFoundError`。

- [x] **Step 3: 实现预检后事务导入**

导入前完成以下全量校验，再开启单一事务：

```python
allowed_event_types = {
    "policy_rate",
    "qe",
    "qt",
    "reinvestment",
    "reserve_management_purchase",
}
```

验证事件 ID 唯一、时期 ID 唯一、`start_date <= end_date`、所有
`source_event_ids` 存在、标签中英文非空、来源为 Federal Reserve 官方域名。

- [x] **Step 4: 提供最小受审计目录**

JSON 仅纳入已有官方链接和已人工复核的事件；每一条包含来源发布时间。
无法确认发布时间的条目不进入目录，也不使用估算日期。

- [x] **Step 5: 运行测试**

Run: `./venv/bin/python -m unittest tests.test_import_policy_catalog tests.test_policy_event_store`

Expected: PASS。

- [x] **Step 6: 提交**

```bash
git add data/fed_policy_catalog_v1.json import_policy_catalog.py \
  tests/test_import_policy_catalog.py
git commit -m "feat: import audited Fed policy catalog"
```

### Task 4: 实现政策时期描述收益

**Files:**
- Create: `research/policy_period_returns.py`
- Create: `tests/test_policy_period_returns.py`

**Interfaces:**
- Produces: `describe_policy_periods(periods, histories, asof, annual_sessions=252) -> pandas.DataFrame`

- [x] **Step 1: 写失败测试，覆盖完整、进行中、未上市和缺口**

```python
def test_complete_period_uses_adjusted_close_and_common_spy_dates():
    # 断言 total_return、annualized_return、relative_spy_return、
    # max_drawdown、positive_month_ratio 和 session_count。

def test_incomplete_period_has_no_rankable_metrics():
    # end_date=None 时 status=incomplete，指标为 NaN。

def test_etf_listed_after_period_is_unavailable_without_proxy():
    # XLRE 历史始于时期结束后，status=not_listed，指标为 NaN。

def test_future_rows_do_not_change_asof_result():
    # 追加 asof 之后价格，旧结果逐字段不变。
```

- [x] **Step 2: 运行测试并确认模块不存在**

Run: `./venv/bin/python -m unittest tests.test_policy_period_returns`

Expected: FAIL with `ModuleNotFoundError`。

- [x] **Step 3: 实现描述计算**

只接受单调递增、唯一日期索引和有限正数复权收盘价。收益端点取时期内首末实际
交易日；相对收益使用同一对共同交易日；最大回撤基于时期内累计复权净值；上涨
月比例使用完整月末，不计当前未完成月。

- [x] **Step 4: 运行单元测试**

Run: `./venv/bin/python -m unittest tests.test_policy_period_returns`

Expected: PASS。

- [x] **Step 5: 提交**

```bash
git add research/policy_period_returns.py tests/test_policy_period_returns.py
git commit -m "feat: describe policy period returns"
```

### Task 5: 生成第一阶段覆盖率审计

**Files:**
- Create: `research/run_policy_catalog_audit.py`
- Create: `tests/test_run_policy_catalog_audit.py`
- Generate: `reports/policy-catalog-audit.json`
- Generate: `reports/policy-catalog-audit.md`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: 政策事件/时期只读接口、本地 ETF 复权历史、`describe_policy_periods`
- Produces: 严格 JSON 报告和人类可读 Markdown；不产生模型分数

- [x] **Step 1: 写失败测试，冻结审计报告契约**

```python
def test_report_keeps_missing_and_incomplete_rows():
    # 断言 missing_history、not_listed、incomplete 不会被过滤。

def test_report_has_no_model_score_or_online_authority():
    # 断言 report_type=descriptive_policy_audit，
    # decision_permission=advisory，online_authority=none，
    # 且没有 probability/recommendation 字段。
```

- [x] **Step 2: 运行测试并确认 runner 不存在**

Run: `./venv/bin/python -m unittest tests.test_run_policy_catalog_audit`

Expected: FAIL with `ModuleNotFoundError`。

- [x] **Step 3: 实现可重复审计 runner**

报告固定包含：

```python
{
    "task_key": "MACRO-ROTATION-001",
    "report_type": "descriptive_policy_audit",
    "lifecycle": "research",
    "decision_permission": "advisory",
    "online_authority": "none",
    "asof": "...",
    "catalog_version": "...",
    "event_counts": {},
    "period_coverage": {},
    "etf_coverage": {},
    "limitations": [],
}
```

JSON 使用 `allow_nan=False`；缺失数值序列化为 `null`；不写本机绝对路径。

- [x] **Step 4: 运行审计并更新中文全局 TODO**

只有在真实目录与本地数据成功生成报告后，才勾选“存储契约”和“描述审计”
子项；不得把完整 `MACRO-ROTATION-001` 标为完成。

- [x] **Step 5: 运行阶段回归**

Run: `./venv/bin/python -m unittest tests.test_web_macro_store tests.test_fetch_macro_data tests.test_policy_event_store tests.test_import_policy_catalog tests.test_policy_period_returns tests.test_run_policy_catalog_audit tests.test_macro_risk tests.test_web_macro_risk_service`

Expected: PASS。

- [x] **Step 6: 做产物与秘密检查**

Run: `git diff --check`

Run: `rg -n '(/Users/|api[_-]?key|secret|token)' reports/policy-catalog-audit.*`

Expected: 第一个命令无输出；第二个命令无匹配。

- [x] **Step 7: 提交**

```bash
git add research/run_policy_catalog_audit.py \
  tests/test_run_policy_catalog_audit.py \
  reports/policy-catalog-audit.json reports/policy-catalog-audit.md \
  docs/modeling-todo.md
git commit -m "research: audit policy catalog coverage"
```

### Task 6: 合并前全量验证

**Files:**
- Verify only

**Interfaces:**
- Consumes: Tasks 1–5 的全部提交
- Produces: 可复核的最终测试证据

- [x] **Step 1: 运行完整测试**

Run: `./venv/bin/python -m unittest discover -s tests`

Expected: PASS，0 failures，0 errors。

- [x] **Step 2: 核对权限与主分支差异**

Run: `rg -n 'MACRO-ROTATION-001|online_authority|decision_permission' research web reports docs/modeling-todo.md`

Expected: 新输出保持 `research/advisory/none`，未修改 Ridge 与最终决策策略。

- [x] **Step 3: 核对运行时文件未入库**

Run: `git status --short`

Expected: 不包含 `*.db-wal`、`*.db-shm` 或 `research/high_level_reversal_study.py`。

- [x] **Step 4: 按分支完成流程集成**

使用 `superpowers:finishing-a-development-branch`；仅在 fresh 验证通过后合入
`main`。
