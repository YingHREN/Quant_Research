# 宏观历史图表看板 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在市场页增加严格点时的宏观历史双图、日期锁定证据和 SPY/QQQ 对照。

**Architecture:** `research/macro_risk.py` 生成单日可审计快照，`MacroHistoryService` 负责按基准交易日组装和缓存，Flask 暴露只读 API，独立 JavaScript 图表模块负责双图同步与锁定。

**Tech Stack:** Python 3.9、pandas、SQLite、Flask、原生 ES modules、Lightweight Charts 5、unittest、Node。

## Global Constraints

- 不改变 `macro_risk_v1` 规则、Ridge 或最终方向。
- 历史点只能使用该日期 UTC 日末以前已经发布的数据版本。
- 覆盖率低于 70% 时保留日期但综合分为空。
- 前端不得重新计算模型分数。
- 图表交互不得平移或缩放整个网页。

---

### Task 1: 点时宏观快照序列

**Files:**
- Modify: `research/macro_risk.py`
- Modify: `tests/test_macro_risk.py`

**Interfaces:**
- Produces: `build_macro_history_rows(observations, dates) -> list[dict]`。

- [ ] 写失败测试：后发布修订不能改变旧日期，输出必须包含分项、原始/派生
  序列元数据和十项证据。
- [ ] 运行 `python -m unittest tests.test_macro_risk -v`，确认缺少接口而失败。
- [ ] 实现复用冻结规则的历史快照构建，不复制阈值。
- [ ] 重跑测试并确认通过。
- [ ] 提交 `feat: build point-in-time macro history rows`。

### Task 2: 宏观历史服务与 API

**Files:**
- Create: `web/services/macro_history.py`
- Modify: `web/services/macro_risk.py`
- Modify: `web/app.py`
- Modify: `web/static/js/api.js`
- Create: `tests/test_web_macro_history.py`
- Modify: `tests/test_web_macro_risk_service.py`

**Interfaces:**
- Produces: `MacroHistoryService.build(asof=None, range_key="3y", benchmark="SPY") -> dict`。
- Produces: `GET /api/macro-history` 和 `getMacroHistory(options)`。

- [ ] 写失败测试：范围裁剪、SPY/QQQ 校验、空数据库降级和数据库更新后
  unavailable 缓存失效。
- [ ] 运行目标测试，确认服务/API 不存在而失败。
- [ ] 实现服务、缓存版本键、路由和 API 客户端。
- [ ] 重跑目标测试并确认通过。
- [ ] 提交 `feat: expose macro history api`。

### Task 3: 双层宏观图与日期锁定

**Files:**
- Create: `web/static/js/macro_history.js`
- Modify: `web/static/js/market.js`
- Modify: `web/templates/market.html`
- Modify: `web/static/css/market.css`
- Modify: `web/static/js/i18n.js`
- Create: `tests/js/macro_history_test.mjs`
- Modify: `tests/test_web_market_assets.py`

**Interfaces:**
- Produces: `createMacroHistoryCharts(scoreEl, contextEl, detailEl, options)`。
- Produces: `setData(payload)`, `setSeries(key)`, `setLocale(locale)`,
  `unlock()`, `destroy()`。

- [ ] 写失败测试：最近日期选择、点击锁定、解锁和序列切换保持同一日期。
- [ ] 运行 Node 与资产测试，确认模块/DOM 不存在而失败。
- [ ] 实现双图、阈值线、同步范围/十字线、锁定明细和响应式样式。
- [ ] 补齐中英文文案并接入请求竞态保护。
- [ ] 重跑 Node、资产和 Python 相关测试。
- [ ] 提交 `feat: add macro history charts`。

### Task 4: 真实数据与浏览器验收

**Files:**
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: 本地 `data/macro_data.db` 与市场数据库。

- [ ] 用本地数据请求 3 年和全部区间，审计首尾日期、覆盖率、修订禁入和
  SPY/QQQ 行数。
- [ ] 启动隔离服务，用浏览器验证曲线、控件、锁定、证据和窄屏。
- [ ] 运行 `LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error python -m unittest discover -s tests -q`。
- [ ] 将中文 TODO 对应项标记完成并记录验收事实。
- [ ] 提交、合入 `main`，在 `main` 再跑全量测试并重启服务。
