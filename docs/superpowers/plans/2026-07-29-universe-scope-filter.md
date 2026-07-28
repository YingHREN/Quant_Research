# 股票池范围筛选 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加默认“全部股票”的互斥范围选择器，使 VCP、紧密平台及后续筛选条件可以作用于全部 1043 只股票。

**Architecture:** 保持 `/api/universe` 不变，在前端 `filters.poolScope` 中保存唯一范围值，由 `filterTickers` 先执行范围判定，再叠加现有形态、门槛、RS、新鲜度和板块条件。模板使用原生 `<select>`，`app.js` 只负责把选择变化写入 store 并重绘股票列表。

**Tech Stack:** Flask/Jinja 模板、原生 ES modules、Node 运行时测试、Python `unittest`、现有暗色 CSS 与 i18n 模块。

## Global Constraints

- 范围值固定为 `all`、`active`、`research`、`catalog`。
- 默认、空值和未知值均按 `all` 处理。
- `research` 包含主动池与研究池的重叠股票。
- `catalog` 仅包含 `research_catalog=true`、`research=false`、`active=false` 的股票。
- 不改变后端 API、研究池增删接口或成员数据结构。
- 不触碰现有未提交的 `docs/modeling-todo.md` 和数据库 WAL/SHM 文件。

---

### Task 1: 统一股票范围筛选语义

**Files:**
- Modify: `web/static/js/universe.js:23-63`
- Test: `tests/test_web_assets.py:430-545`

**Interfaces:**
- Consumes: `row.pool_membership` 或兼容别名 `row.poolMembership`。
- Produces: `filterTickers(rows, query, {poolScope, ...filters}) -> Array<Row>`。

- [ ] **Step 1: 将现有股票池筛选测试改为四个互斥范围**

  在 `test_universe_helpers_filter_sort_and_preserve_inputs` 的 Node 脚本中，用以下断言替换 `activePool`、`researchOnly` 和 `eitherPool`：

  ```javascript
  const allPool = filterTickers(rows, '', {poolScope: 'all'})
    .map(row => row.ticker);
  const activePool = filterTickers(rows, '', {poolScope: 'active'})
    .map(row => row.ticker);
  const researchPool = filterTickers(rows, '', {poolScope: 'research'})
    .map(row => row.ticker);
  const catalogPool = filterTickers(rows, '', {poolScope: 'catalog'})
    .map(row => row.ticker);
  const unknownPool = filterTickers(rows, '', {poolScope: 'future-value'})
    .map(row => row.ticker);
  const catalogVcp = filterTickers(
    rows, '', {poolScope: 'catalog', strictVcp: true}
  ).map(row => row.ticker);
  ```

  使用现有三只股票的手工期望值，证明全部、主动、研究、候选库、未知值回退及范围与 VCP 叠加均正确。

- [ ] **Step 2: 运行测试并确认 RED**

  Run:

  ```bash
  PYTHONPYCACHEPREFIX=/private/tmp/pool-scope-red \
    venv/bin/python -m unittest \
    tests.test_web_assets.WebAssetTest.test_universe_helpers_filter_sort_and_preserve_inputs -v
  ```

  Expected: FAIL，因为 `filterTickers` 尚未读取 `poolScope`，四种范围都会返回相同股票。

- [ ] **Step 3: 实现范围归一化与单一范围分支**

  在 `universe.js` 中增加：

  ```javascript
  const POOL_SCOPES = new Set(["all", "active", "research", "catalog"]);

  function normalizedPoolScope(value) {
    const scope = String(value || "all");
    return POOL_SCOPES.has(scope) ? scope : "all";
  }

  function matchesPoolScope(membership, scope) {
    if (scope === "active") return Boolean(membership.active);
    if (scope === "research") return Boolean(membership.research);
    if (scope === "catalog") {
      return Boolean(
        membership.research_catalog
        && !membership.research
        && !membership.active
      );
    }
    return true;
  }
  ```

  在 `filterTickers` 中删除 `activePool`/`researchOnly` 的数组组合逻辑，改为一次 `matchesPoolScope` 判定。

- [ ] **Step 4: 运行测试并确认 GREEN**

  重复 Step 2 命令。Expected: PASS。

- [ ] **Step 5: 提交纯筛选逻辑**

  ```bash
  git add web/static/js/universe.js tests/test_web_assets.py
  git commit -m "web: add exclusive universe scope filtering"
  ```

### Task 2: 增加可本地化的范围选择器

**Files:**
- Modify: `web/templates/index.html:115-129`
- Modify: `web/static/js/app.js:1083-1110,1190-1200`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `tests/dashboard_runtime.mjs`
- Test: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: `<select id="pool-scope">` 的 `value`。
- Produces: `store.filters.poolScope`，初始/默认值为 `all`；变化后只调用 `paintUniverse()`。

- [ ] **Step 1: 添加模板和双语契约的失败测试**

  在 `test_bilingual_dashboard` 中断言：

  ```python
  self.assertIn('id="pool-scope"', html)
  self.assertIn('value="all"', html)
  self.assertIn('data-i18n="universe.poolScope.all"', html)
  self.assertNotIn('data-filter="activePool"', html)
  self.assertNotIn('data-filter="researchOnly"', html)
  ```

  并要求 `i18n.js` 包含：

  ```text
  universe.poolScope.label
  universe.poolScope.all
  universe.poolScope.active
  universe.poolScope.research
  universe.poolScope.catalog
  ```

- [ ] **Step 2: 添加实际仪表盘范围变化的失败测试**

  在 `dashboard_runtime.mjs` 中注册 `pool-scope` 元素，为测试 universe 增加至少一只主动股票、一只研究股票和一只候选库股票。新增 `pool-scope` 模式：

  ```javascript
  const scope = elements.get("pool-scope");
  assert.equal(scope.value, "all");
  scope.value = "research";
  scope.dispatch("change");
  assert.equal(elements.get("universe-count").textContent, "1/3");
  scope.value = "catalog";
  scope.dispatch("change");
  assert.equal(elements.get("universe-count").textContent, "1/3");
  assert.equal(stockAttempts, 1);
  ```

  `tests/test_web_assets.py` 新增包装测试，断言最终范围、数量及股票详情请求次数。

- [ ] **Step 3: 运行 UI 测试并确认 RED**

  Run:

  ```bash
  PYTHONPYCACHEPREFIX=/private/tmp/pool-scope-ui-red \
    venv/bin/python -m unittest \
    tests.test_web_assets.WebAssetTest.test_bilingual_dashboard \
    tests.test_web_assets.WebAssetTest.test_dashboard_pool_scope_filters_without_reloading_stock -v
  ```

  Expected: FAIL，因为模板、元素绑定、翻译键和事件处理尚不存在。

- [ ] **Step 4: 实现模板、状态绑定和双语文案**

  在筛选 fieldset 顶部加入：

  ```html
  <label class="filter-scope" for="pool-scope">
    <span data-i18n="universe.poolScope.label">股票范围</span>
    <select id="pool-scope">
      <option value="all" data-i18n="universe.poolScope.all">全部股票</option>
      <option value="active" data-i18n="universe.poolScope.active">主动池</option>
      <option value="research" data-i18n="universe.poolScope.research">研究池</option>
      <option value="catalog" data-i18n="universe.poolScope.catalog">候选库</option>
    </select>
  </label>
  ```

  删除旧池复选框。`captureElements` 增加 `poolScope: byId("pool-scope")`；`bindControls`
  增加 change 监听，将 `event.currentTarget.value || "all"` 写入
  `filters.poolScope` 后调用 `paintUniverse()`。初始化时模板的 `all` 即默认值，
  `filterTickers` 对缺省状态也回退到 `all`。

  中文使用“股票范围 / 全部股票 / 主动池 / 研究池 / 候选库”，英文使用
  “Stock scope / All stocks / Active pool / Research pool / Catalog”。

- [ ] **Step 5: 增加窄侧栏样式**

  为 `.filter-scope` 设置跨满 fieldset 的网格列、纵向标签间距，并令其中
  `select` 使用 `width: 100%`，不得改变其余复选框布局。

- [ ] **Step 6: 运行 UI 测试并确认 GREEN**

  重复 Step 3 命令。Expected: PASS。

- [ ] **Step 7: 提交界面集成**

  ```bash
  git add web/templates/index.html web/static/js/app.js \
    web/static/js/i18n.js web/static/css/dashboard.css \
    tests/dashboard_runtime.mjs tests/test_web_assets.py
  git commit -m "web: expose all-stock scope selector"
  ```

### Task 3: 回归与实际页面验证

**Files:**
- Verify only; no new production files expected.

**Interfaces:**
- Consumes: Task 1 与 Task 2 的完整前端行为。
- Produces: 可运行的 `main` 服务和浏览器验证证据。

- [ ] **Step 1: 运行相关前端回归**

  ```bash
  PYTHONPYCACHEPREFIX=/private/tmp/pool-scope-regression \
    venv/bin/python -m unittest tests.test_web_assets -v
  ```

  Expected: all tests PASS。

- [ ] **Step 2: 运行完整测试**

  ```bash
  PYTHONPYCACHEPREFIX=/private/tmp/pool-scope-full \
    venv/bin/python -m unittest discover -s tests -v
  ```

  Expected: all functional tests PASS；若既有 35 秒性能门槛仍仅因本机耗时波动失败，
  单独报告真实耗时，不将其归因于本次纯前端筛选改动。

- [ ] **Step 3: 重启服务并验证交互**

  重启 `venv/bin/python web/app.py`，在 `http://127.0.0.1:5000/` 验证：

  1. 初始范围为“全部股票”，数量分母为 1043。
  2. 选择“全部股票 + 严格 VCP”可从全部股票中筛选。
  3. 切换“主动池 / 研究池 / 候选库”时列表与显示数量变化。
  4. 切换范围不重新请求当前股票详情。
  5. 中英文切换后标签和选项更新。
  6. 浏览器控制台无错误。

- [ ] **Step 4: 检查工作区并提交遗漏**

  ```bash
  git diff --check
  git status --short --branch
  ```

  不提交或删除用户已有的 `docs/modeling-todo.md`、数据库 WAL/SHM 文件和
  `research/high_level_reversal_study.py`。
