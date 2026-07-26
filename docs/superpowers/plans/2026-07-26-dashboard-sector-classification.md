# 看板板块分类与筛选实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不加载研究库日线的前提下，为当前看板股票池增加 SEC/ETF 行为板块元数据、筛选、成员数量和冲突解释。

**Architecture:** 新建只读 `ResearchClassificationService`，批量读取研究库证券与分类表并返回稳定 DTO；现有 `UniverseSnapshotService` 只负责合并分类。前端在当前内存股票池上过滤，选中股票时从同一 DTO 渲染分类卡，不增加行情请求。

**Tech Stack:** Python 3.9、SQLite、Flask、原生 ES modules、HTML/CSS、`unittest`、Node。

## Global Constraints

- 默认只展示现有轻量股票池，禁止从 `/api/universe` 读取 `daily_prices`。
- 研究库不可用时原股票池必须继续工作。
- 未分类必须显式显示，不允许猜测或回退到另一套分类。
- 所有新增文案同时提供中文和英文。
- 动态内容只能通过 DOM `textContent`/节点 API 渲染，禁止 HTML 字符串注入。

---

### Task 1: 只读分类服务

**Files:**
- Create: `web/services/research_classification.py`
- Create: `tests/test_web_research_classification.py`

**Interfaces:**
- Produces: `ResearchClassificationService(database_path, max_cache_size=2)`
- Produces: `ResearchClassificationService.build(tickers) -> {"status", "asof", "research_universe_count", "sector_counts", "by_ticker"}`

- [ ] **Step 1: 写失败测试**

用临时 SQLite 建立 `security_master`、`universe_memberships`、`sector_classifications`，断言：

```python
payload = service.build(["AAA", "BBB", "MISSING"])
self.assertEqual(payload["by_ticker"]["AAA"]["state"], "agree")
self.assertEqual(payload["by_ticker"]["BBB"]["state"], "conflict")
self.assertEqual(payload["by_ticker"]["MISSING"]["state"], "unclassified")
self.assertEqual(payload["sector_counts"]["sec"]["technology"], 2)
```

另写数据库缺失测试，断言 `status == "unavailable"` 且每个 ticker 为 `unclassified`。

- [ ] **Step 2: 运行测试并确认因模块缺失失败**

Run: `./venv/bin/python -m unittest tests.test_web_research_classification`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: 实现最小服务**

只读连接 SQLite，单次批量查询传入 ticker，再用两个 `GROUP BY taxonomy, sector_key` 查询研究池数量；DTO 保留来源、规则版本、asof、置信度、benchmark、相关性、beta、63 日相对收益、样本数和冲突原因。

- [ ] **Step 4: 运行服务测试**

Run: `./venv/bin/python -m unittest tests.test_web_research_classification`

Expected: PASS.

### Task 2: 合并到 Universe API

**Files:**
- Modify: `web/services/universe.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `ResearchClassificationService.build(tickers)`
- Produces: 顶层 `classification_summary`
- Produces: 每行 `sector_classification`

- [ ] **Step 1: 写失败测试**

向 `UniverseSnapshotService` 注入固定分类服务，断言返回行包含 `sector_classification`，顶层包含 `classification_summary`，并断言分类服务收到一次完整 ticker 列表。

- [ ] **Step 2: 运行测试并确认字段缺失**

Run: `./venv/bin/python -m unittest tests.test_web_universe_service tests.test_web_api.WebApiTest.test_universe_schema_and_repository_calls`

Expected: FAIL because classification fields are absent.

- [ ] **Step 3: 实现合并与 Flask 配置**

增加 `RESEARCH_DATABASE=data/research_prices.db`，工厂允许注入 `RESEARCH_CLASSIFICATION_SERVICE`。分类不可用时仍返回原股票列表和 unavailable summary。

- [ ] **Step 4: 运行 API 测试**

Run: `./venv/bin/python -m unittest tests.test_web_universe_service tests.test_web_api.WebApiTest.test_universe_schema_and_repository_calls`

Expected: PASS.

### Task 3: 浏览器端过滤与分类说明

**Files:**
- Modify: `web/static/js/universe.js`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Produces: `filterTickers(rows, query, filters)` 支持 `sectorTaxonomy` 与 `sectorKey`
- Produces: `classificationFor(row, taxonomy)` 与 `classificationState(row)`

- [ ] **Step 1: 写失败 Node 测试**

构造一致、冲突、SEC-only 和未分类四行，断言：

```javascript
filterTickers(rows, "", {sectorTaxonomy: "sec", sectorKey: "technology"})
filterTickers(rows, "", {sectorTaxonomy: "market_behavior", sectorKey: "unclassified"})
```

返回正确 ticker，且原数组未改变。

- [ ] **Step 2: 运行并确认筛选失败**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_universe_helpers_filter_sort_and_preserve_inputs`

Expected: FAIL because sector filters are ignored.

- [ ] **Step 3: 实现过滤和行内板块标签**

保持现有筛选兼容；`renderUniverse` 根据 `options.sectorTaxonomy` 显示板块标签，未分类使用 i18n 文案。

- [ ] **Step 4: 运行资产测试**

Run: `./venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_universe_helpers_filter_sort_and_preserve_inputs`

Expected: PASS.

### Task 4: 页面控件与选中股票分类卡

**Files:**
- Modify: `web/templates/index.html`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `classification_summary` 与行级 `sector_classification`
- Produces: `#sector-taxonomy`、`#sector-key`、`#sector-membership-summary`、`#security-classification`

- [ ] **Step 1: 写失败资产测试**

断言 HTML 包含四个稳定 ID，中文/英文包含 SEC、ETF 行为、置信度、共同样本、冲突、未分类文案。

- [ ] **Step 2: 运行并确认标记缺失**

Run: `./venv/bin/python -m unittest tests.test_web_assets`

Expected: FAIL on missing IDs or i18n keys.

- [ ] **Step 3: 实现控件、分类卡和样式**

板块选项从后端 summary 动态生成；切换口径或板块只调用 `paintUniverse()`。分类卡使用 `createElement` 和 `textContent`，显示两套分类、行为参考 ETF、置信度、样本数与冲突原因。

- [ ] **Step 4: 运行资产测试**

Run: `./venv/bin/python -m unittest tests.test_web_assets`

Expected: PASS.

### Task 5: 验证、文档与提交

**Files:**
- Modify: `docs/modeling-todo.md`

- [ ] **Step 1: 运行完整测试**

Run: `PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests PASS.

- [ ] **Step 2: 运行静态和数据库边界检查**

Run: `./venv/bin/python -m py_compile web/services/research_classification.py web/services/universe.py web/app.py`

Expected: exit 0；并确认 `/api/universe` 查询路径不引用 `daily_prices`。

- [ ] **Step 3: 启动本地服务并做浏览器视觉验证**

验证中文/英文、分类口径切换、板块过滤、未分类、冲突卡和窄屏布局。

- [ ] **Step 4: 更新 TODO 并提交**

把 DATA-001 对应 UI 项标记完成，记录 API 不加载日线和降级行为。

Run:

```bash
git add web tests docs/modeling-todo.md
git commit -m "feat: show sector classifications in dashboard"
```
