# 政策时期板块矩阵实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有市场页提供点时、只读、可审计的“政策时期 × SPY/QQQ/11 个板块 ETF”历史描述矩阵，并明确区分完整时期、进行中时期、未上市和缺失数据。

**Architecture:** 复用 `PolicyEventStore`、`describe_policy_periods()` 和市场快照中的复权 ETF 历史，在请求时构造 39 行以内的描述载荷，并按价格库 revision、政策数据库 token 和 `asof` 做有界缓存。市场 API 只增加独立 `policy_period_matrix` 字段；前端切换指标和时期只格式化后端结果，不排名、不推断政策状态、不修改 Ridge。

**Tech Stack:** Python 3.9、Flask、SQLite 只读访问、pandas、原生 ES modules、CSS Grid、`unittest`、Node 语法/运行时测试。

## Global Constraints

- 任务编号固定为 `MACRO-ROTATION-001`。
- 设计来源固定为 `docs/superpowers/specs/2026-07-30-point-in-time-policy-sector-rotation-design.md`。
- 标准证券集合固定为 `SPY QQQ XLK XLC XLY XLP XLE XLF XLV XLI XLB XLRE XLU`。
- 指标固定为总收益、年化收益、相对 SPY 超额收益、最大回撤和月度上涨比例。
- 所有政策事件和时期只允许 `available_at <= asof`。
- 进行中时期、尚未上市 ETF、缺失历史和不足历史必须保持非数值状态，不得填 0、代理回填或参与排名。
- 该层固定为 `lifecycle=research`、`decision_permission=advisory`、`online_authority=none`。
- 输出只描述历史，不产生概率、预测分、板块推荐、Ridge 调整或向下否决。
- 打开页面不得导入目录、下载行情、初始化缺失数据库或修改任何原始数据文件。
- 前端不得重新计算收益、状态或完成度；中英文文案必须同时提供。

---

### Task 1: 构造严格的矩阵载荷

**Files:**
- Create: `research/policy_period_matrix.py`
- Create: `tests/test_policy_period_matrix.py`
- Reuse: `research/policy_period_returns.py`

**Interfaces:**
- Consumes: `build_policy_period_matrix(periods, events, histories, asof)`
- Produces: JSON-safe mapping with `artifact_key`, `asof`, `periods`, `rows`, `metrics`, `coverage`, lifecycle and authority fields

- [ ] **Step 1: 写失败测试，冻结载荷结构和权限**

```python
class PolicyPeriodMatrixTest(unittest.TestCase):
    def test_complete_period_exposes_metrics_without_ranking(self):
        payload = build_policy_period_matrix(
            periods=period_fixture(),
            events=event_fixture(),
            histories=history_fixture(),
            asof="2026-07-29T23:59:59+00:00",
        )
        self.assertEqual(payload["artifact_key"], "policy_period_matrix_v1")
        self.assertEqual(payload["lifecycle"], "research")
        self.assertEqual(payload["decision_permission"], "advisory")
        self.assertEqual(payload["online_authority"], "none")
        self.assertNotIn("score", payload)
        self.assertNotIn("recommendation", payload)
        complete = next(
            row for row in payload["rows"]
            if row["period_id"] == "complete" and row["ticker"] == "XLK"
        )
        self.assertEqual(complete["status"], "complete")
        self.assertAlmostEqual(complete["total_return"], 0.10)
```

- [ ] **Step 2: 写失败测试，覆盖进行中、未上市、缺失和官方事件引用**

```python
    def test_non_complete_rows_keep_metrics_null(self):
        payload = build_policy_period_matrix(...)
        for row in payload["rows"]:
            if row["status"] != "complete":
                self.assertIsNone(row["total_return"])
                self.assertIsNone(row["relative_spy_return"])

    def test_period_detail_resolves_only_known_source_events(self):
        period = build_policy_period_matrix(... )["periods"][0]
        self.assertEqual(period["source_event_ids"], ["event-a"])
        self.assertEqual(period["events"][0]["source_url"], OFFICIAL_URL)

    def test_future_event_and_period_are_invisible_at_asof(self):
        historical = build_policy_period_matrix(..., asof="2024-01-01")
        self.assertNotIn("future-period", {
            row["period_id"] for row in historical["rows"]
        })
```

- [ ] **Step 3: 运行测试并确认因模块不存在而失败**

Run:

```bash
./venv/bin/python -m unittest tests.test_policy_period_matrix -v
```

Expected: FAIL with `ModuleNotFoundError: research.policy_period_matrix`.

- [ ] **Step 4: 实现 JSON 安全适配和时期详情**

```python
MATRIX_ARTIFACT_KEY = "policy_period_matrix_v1"
MATRIX_METRICS = (
    "total_return",
    "annualized_return",
    "relative_spy_return",
    "max_drawdown",
    "positive_month_ratio",
)

def build_policy_period_matrix(periods, events, histories, asof):
    described = describe_policy_periods(periods, histories, asof)
    rows = [_matrix_row(row) for row in described.to_dict("records")]
    return {
        "artifact_key": MATRIX_ARTIFACT_KEY,
        "asof": _utc_iso(asof),
        "periods": _period_details(periods, events),
        "rows": rows,
        "metrics": list(MATRIX_METRICS),
        "coverage": _coverage(rows),
        "lifecycle": "research",
        "decision_permission": "advisory",
        "online_authority": "none",
        "point_in_time": True,
        "historical_description_only": True,
        "unavailable_reason": None if rows else "policy_periods_unavailable",
    }
```

`_matrix_row()` 必须把 `NaN`/`inf` 转为 `None`；`_period_details()` 解析
`source_event_ids_json`，只连接输入 `events` 中真实存在的事件，不伪造来源。

- [ ] **Step 5: 运行测试并提交**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_policy_period_returns \
  tests.test_policy_period_matrix -v
```

Expected: PASS.

Commit:

```bash
git add research/policy_period_matrix.py tests/test_policy_period_matrix.py
git commit -m "feat: build policy period sector matrix"
```

---

### Task 2: 增加只读矩阵服务和缓存契约

**Files:**
- Create: `web/services/policy_period_matrix.py`
- Create: `tests/test_web_policy_period_matrix_service.py`
- Reuse: `web/services/policy_event_store.py`

**Interfaces:**
- Consumes: `PolicyPeriodMatrixService.build(asof, histories)`
- Produces: Task 1 的稳定载荷；`cache_token()` 供市场概览缓存键使用

- [ ] **Step 1: 写失败测试，覆盖真实存储读取和缓存副本**

```python
class PolicyPeriodMatrixServiceTest(unittest.TestCase):
    def test_build_reads_visible_catalog_and_returns_fresh_copy(self):
        store = initialized_policy_store()
        service = PolicyPeriodMatrixService(store.path, max_cache_size=4)
        first = service.build("2026-07-29", history_fixture())
        first["rows"][0]["status"] = "tampered"
        second = service.build("2026-07-29", history_fixture())
        self.assertNotEqual(second["rows"][0]["status"], "tampered")

    def test_cache_token_changes_when_policy_database_changes(self):
        before = service.cache_token()
        store.upsert_events([new_event()])
        self.assertNotEqual(service.cache_token(), before)
```

- [ ] **Step 2: 写失败测试，冻结缺库和非法容量行为**

```python
    def test_missing_database_is_not_created(self):
        payload = PolicyPeriodMatrixService(missing_path).build(
            "2026-07-29", history_fixture()
        )
        self.assertEqual(
            payload["unavailable_reason"],
            "policy_catalog_unavailable",
        )
        self.assertFalse(missing_path.exists())

    def test_cache_size_must_be_positive_integer(self):
        with self.assertRaises(ValueError):
            PolicyPeriodMatrixService(path, max_cache_size=0)
```

- [ ] **Step 3: 运行测试并确认失败**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_policy_period_matrix_service -v
```

Expected: FAIL because the service does not exist.

- [ ] **Step 4: 实现服务**

```python
class PolicyPeriodMatrixService:
    def __init__(self, database_path, max_cache_size=32):
        _validate_cache_size(max_cache_size)
        self._path = Path(database_path)
        self._store = PolicyEventStore(self._path)
        self._cache = {}
        self._lock = RLock()

    def build(self, asof, histories):
        cutoff = _asof_cutoff(asof)
        key = (
            self.cache_token(),
            cutoff.isoformat(),
            _history_identity(histories),
        )
        # cache hit returns deepcopy
        # load_events/load_periods are read-only
        # PolicyDataUnavailable returns typed unavailable payload
```

`_history_identity()` 只基于标准 ETF 的日期边界、行数及末行复权收盘构造确定性
元组；市场概览外层还会用价格库 revision 失效。不得对大型 DataFrame 做 JSON
序列化或写磁盘缓存。

- [ ] **Step 5: 运行测试并提交**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_policy_event_store \
  tests.test_web_policy_period_matrix_service -v
```

Expected: PASS.

Commit:

```bash
git add web/services/policy_period_matrix.py \
  tests/test_web_policy_period_matrix_service.py
git commit -m "feat: serve cached policy period matrix"
```

---

### Task 3: 接入市场概览 API

**Files:**
- Modify: `web/services/market_overview.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_market_overview.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `PolicyPeriodMatrixService.build(normalized_asof, snapshot.histories)`
- Produces: `/api/market-overview` 顶层 `policy_period_matrix`

- [ ] **Step 1: 写失败服务测试，保证不改变现有输出**

```python
def test_includes_policy_period_matrix_without_changing_scores(self):
    service = MarketOverviewService(
        repository,
        policy_period_matrix_service=MatrixStub(),
    )
    payload = service.build(horizon=5, sector="semiconductor")
    self.assertEqual(
        payload["policy_period_matrix"]["artifact_key"],
        "policy_period_matrix_v1",
    )
    self.assertEqual(payload["market_posture"], EXPECTED_POSTURE)
    self.assertEqual(matrix_stub.received_histories, snapshot.histories)
```

- [ ] **Step 2: 写失败 API 测试，冻结配置注入和缺库降级**

```python
def test_market_api_exposes_research_only_policy_matrix(self):
    response = client.get(
        "/api/market-overview?horizon=5&sector=semiconductor"
    )
    matrix = response.get_json()["policy_period_matrix"]
    self.assertEqual(matrix["decision_permission"], "advisory")
    self.assertEqual(matrix["online_authority"], "none")

def test_missing_policy_catalog_does_not_create_database(self):
    # configure MACRO_DATABASE to a missing path
    # assert API remains 200, typed reason is returned, path is absent
```

- [ ] **Step 3: 运行测试并确认字段缺失**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_overview \
  tests.test_web_api.WebApiTest.test_market_page_and_api_keep_daily_proxy_state_honest \
  -v
```

Expected: FAIL because `policy_period_matrix` is absent.

- [ ] **Step 4: 装配服务并扩展缓存键**

`MarketOverviewService.__init__()` 新增
`policy_period_matrix_service=None`。缓存键加入
`_macro_cache_token(self._policy_period_matrix_service)`；有快照时传入同一份
`snapshot.histories`，空快照返回稳定 unavailable payload。

`create_app()` 从可选 `POLICY_PERIOD_MATRIX_SERVICE` 读取注入；默认使用
`MACRO_DATABASE` 创建只读服务，并注册到
`flask_app.extensions["dashboard_policy_period_matrix_service"]`。

- [ ] **Step 5: 运行测试并提交**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_web_market_overview \
  tests.test_web_api -v
```

Expected: PASS.

Commit:

```bash
git add web/services/market_overview.py web/app.py \
  tests/test_web_market_overview.py tests/test_web_api.py
git commit -m "feat: expose policy matrix in market api"
```

---

### Task 4: 渲染双语历史矩阵和时期证据

**Files:**
- Modify: `web/templates/market.html`
- Modify: `web/static/js/market.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/market.css`
- Modify: `tests/test_web_market_assets.py`
- Create: `tests/policy_matrix_runtime.mjs`

**Interfaces:**
- Consumes: `payload.policy_period_matrix`
- Produces: `#policy-period-matrix`、`#policy-period-detail`、指标按钮 `[data-policy-metric]`

- [ ] **Step 1: 写失败资产测试**

断言模板包含：

```html
id="policy-period-matrix"
id="policy-period-detail"
data-policy-metric="total_return"
data-policy-metric="annualized_return"
data-policy-metric="relative_spy_return"
data-policy-metric="max_drawdown"
data-policy-metric="positive_month_ratio"
```

断言 `market.js` 包含
`renderPolicyPeriodMatrix(payload.policy_period_matrix)`，且不包含
`rankPolicySectors(`、`calculatePolicyReturn(` 或 `innerHTML`。中英文必须覆盖
标题、五个指标、五种缺失状态、历史描述免责声明和权限文案。

- [ ] **Step 2: 写失败 Node 运行时测试**

在 `tests/policy_matrix_runtime.mjs` 构造最小 DOM 和完整载荷，断言：

```javascript
assert.match(textTree(matrix), /XLK/);
assert.match(textTree(matrix), /10.0%/);
assert.match(textTree(detail), /2022-03-17/);
assert.match(textTree(detail), /Historical description/);

metricButton("max_drawdown").dispatch("click");
assert.match(textTree(matrix), /-12.0%/);

render(unavailablePayload);
assert.match(textTree(matrix), /Policy catalog unavailable/);
assert.doesNotMatch(textTree(matrix), /0.0%/);
```

- [ ] **Step 3: 运行测试并确认失败**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_market_assets -v
node tests/policy_matrix_runtime.mjs \
  file://$PWD/web/static/js/market.js
```

Expected: FAIL because the DOM regions and renderer do not exist.

- [ ] **Step 4: 增加稳定布局和指标切换**

在当前政策组合之后、宏观历史之前增加“政策时期历史描述”区域。矩阵固定使用
列为时期、行为 ETF；非完整单元格显示状态标签，不渲染颜色值。完整值按冻结的
指标范围设置 `data-tone="positive|negative|neutral"`，不得把颜色称为推荐。

`state.policyMetric` 默认 `total_return`。按钮只改变该字段并重新调用
`renderPolicyPeriodMatrix(state.payload.policy_period_matrix)`；不得重新请求
API、改变图表尺寸或修改宏观历史日期锁定。

时期详情显示起止日、完整/进行中状态、人工解释、来源事件标题、有效日和官方
链接。动态链接使用 `document.createElement("a")` 和后端 URL，不使用 HTML
字符串。

- [ ] **Step 5: 运行 UI 测试并提交**

Run:

```bash
./venv/bin/python -m unittest tests.test_web_market_assets -v
node tests/policy_matrix_runtime.mjs \
  file://$PWD/web/static/js/market.js
node --check web/static/js/market.js
node --check web/static/js/i18n.js
```

Expected: PASS.

Commit:

```bash
git add web/templates/market.html web/static/js/market.js \
  web/static/js/i18n.js web/static/css/market.css \
  tests/test_web_market_assets.py tests/policy_matrix_runtime.mjs
git commit -m "feat: show policy period sector matrix"
```

---

### Task 5: 运维文档、真实数据验收和全量验证

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/modeling-todo.md`
- Create: `reports/policy-period-matrix-audit.md`
- Create: `reports/policy-period-matrix-audit.json`
- Modify: `research/run_policy_catalog_audit.py` only if the existing report cannot emit the UI contract without duplication

**Interfaces:**
- Documents: catalog import, matrix semantics, data coverage, no-authority boundary
- Verifies: 3 periods × 13 symbols = 39 rows with incomplete rows separated

- [ ] **Step 1: 运行真实只读矩阵验收**

Run:

```bash
source env.sh
./venv/bin/python import_policy_catalog.py
./venv/bin/python research/run_policy_catalog_audit.py \
  --asof 2026-07-29T23:59:59+00:00
```

Expected:

```text
periods=3
rows=39
complete=26
incomplete=13
online_authority=none
```

不得在输出中出现 API key、本机绝对路径、NaN、Infinity、预测分或板块推荐。

- [ ] **Step 2: 更新文档与 TODO**

`docs/dashboard.md` 记录：

```bash
./venv/bin/python import_policy_catalog.py
```

以及矩阵五项指标、缺失状态和历史描述边界。只勾选
`MACRO-ROTATION-001` 中实际完成的“时期描述 API/矩阵 UI”；政策色带、历史
类比、制度切片、RRG、轮动状态机和走步验证继续保持未完成。

- [ ] **Step 3: 运行聚焦验证**

Run:

```bash
./venv/bin/python -m unittest \
  tests.test_import_policy_catalog \
  tests.test_policy_event_store \
  tests.test_policy_period_returns \
  tests.test_policy_period_matrix \
  tests.test_web_policy_period_matrix_service \
  tests.test_web_market_overview \
  tests.test_web_market_assets -v
```

Expected: PASS.

- [ ] **Step 4: 运行完整验证**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache \
  ./venv/bin/python -m unittest discover -s tests
```

Expected: PASS. 如果仅
`test_first_evidence_dates_do_not_create_global_full_builds` 在 35 秒边界轻微
波动，必须先在未修改 main 与功能分支分别单独复跑；不得放宽门槛。

- [ ] **Step 5: 浏览器验收**

启动本地服务后检查 `/market`：

- 宽屏和窄屏无横向页面溢出；
- 五个指标切换不触发网络请求；
- 进行中时期显示“进行中”，不是 `0.0%`；
- 中文和英文切换保留当前指标；
- 宏观历史日期锁定、图表尺寸和可见范围不受矩阵操作影响；
- 页面显示“历史描述，不是预测”和无线上决策权。

- [ ] **Step 6: 提交文档与报告**

```bash
git add docs/dashboard.md docs/modeling-todo.md \
  reports/policy-period-matrix-audit.md \
  reports/policy-period-matrix-audit.json
git commit -m "docs: audit policy period matrix"
```

---

## 下一边界

本计划完成后，下一项是独立的 `policy-band-long-chart`：为 SPY/QQQ 长期图增加
可审计政策时期色带，并复用本计划的时期详情。历史相似月份、制度切片器、连续
板块先验、RRG 和轮动状态机必须继续保持未实现，直到数据覆盖和描述矩阵验收
通过。
