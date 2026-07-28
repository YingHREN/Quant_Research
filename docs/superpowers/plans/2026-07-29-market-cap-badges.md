# 市值标记实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在股票池卡片和所选证券摘要中显示点时一致的“规模等级 · 美元市值”标记，并为未来市值筛选和排序提供稳定 API 字段。

**Architecture:** 从研究目录有效 membership 记录读取原始市值和生效日期，由独立后端纯函数校验并分级，再合并进 universe row 和股票详情 summary。前端只负责本地化等级、紧凑金额格式和展示，不重新估算市值。

**Tech Stack:** Python 3.9、SQLite、Flask、原生 ES modules、HTML/CSS、`unittest`、Node 运行时测试。

## Global Constraints

- 市值来源只能是 `research_prices.db.universe_memberships.market_cap`，页面加载不调用外部 API。
- API 字段固定为 `market_cap`、`market_cap_asof`、`market_cap_tier`。
- 分级边界固定为：超大盘 `>= 200B`，大盘 `10B–200B`，中盘 `2B–10B`，小盘 `300M–2B`，微盘 `0–300M`。
- 缺失、非有限、零或负市值必须显式不可用，不能显示成 `$0`。
- ETF 的基金规模不作为公司市值回填。
- 本轮不增加市值筛选控件或排序选项。

---

### Task 1: 市值校验与分级契约

**Files:**
- Create: `web/services/market_cap.py`
- Create: `tests/test_web_market_cap.py`

**Interfaces:**
- Consumes: 原始 `market_cap` 和 `market_cap_asof`。
- Produces: `market_cap_fields(value, asof) -> dict`，返回三个固定 API 字段。

- [ ] **Step 1: 写失败的边界测试**

```python
from web.services.market_cap import market_cap_fields


def test_market_cap_fields_use_exact_tier_boundaries():
    assert market_cap_fields(200_000_000_000, "2026-07-24") == {
        "market_cap": 200_000_000_000.0,
        "market_cap_asof": "2026-07-24",
        "market_cap_tier": "mega",
    }
    assert market_cap_fields(10_000_000_000, "2026-07-24")["market_cap_tier"] == "large"
    assert market_cap_fields(2_000_000_000, "2026-07-24")["market_cap_tier"] == "mid"
    assert market_cap_fields(300_000_000, "2026-07-24")["market_cap_tier"] == "small"
    assert market_cap_fields(299_999_999, "2026-07-24")["market_cap_tier"] == "micro"


def test_market_cap_fields_fail_closed_for_missing_or_invalid_values():
    unavailable = {
        "market_cap": None,
        "market_cap_asof": None,
        "market_cap_tier": "unavailable",
    }
    for value in (None, 0, -1, float("nan"), float("inf"), "bad"):
        assert market_cap_fields(value, "2026-07-24") == unavailable
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_market_cap
```

Expected: FAIL，原因是 `web.services.market_cap` 尚不存在。

- [ ] **Step 3: 实现最小纯函数**

```python
"""Point-in-time company market-cap API fields."""

from __future__ import annotations

import math

from web.contracts import iso_date


def market_cap_fields(value, asof):
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = None
    if normalized is None or not math.isfinite(normalized) or normalized <= 0:
        return {
            "market_cap": None,
            "market_cap_asof": None,
            "market_cap_tier": "unavailable",
        }
    tier = (
        "mega" if normalized >= 200_000_000_000
        else "large" if normalized >= 10_000_000_000
        else "mid" if normalized >= 2_000_000_000
        else "small" if normalized >= 300_000_000
        else "micro"
    )
    return {
        "market_cap": normalized,
        "market_cap_asof": iso_date(asof),
        "market_cap_tier": tier,
    }
```

- [ ] **Step 4: 运行测试并确认 GREEN**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_market_cap
```

Expected: 所有测试通过。

- [ ] **Step 5: 提交**

```bash
git add web/services/market_cap.py tests/test_web_market_cap.py
git commit -m "web: define market cap tiers"
```

---

### Task 2: 点时研究目录读取

**Files:**
- Modify: `web/services/research_universe.py`
- Modify: `tests/test_web_research_universe.py`

**Interfaces:**
- Consumes: Task 1 的原始值契约；SQLite `universe_memberships`。
- Produces: `ResearchUniverseMember.market_cap: float | None` 与 `market_cap_asof: str | None`。

- [ ] **Step 1: 扩展 fixture 并写失败测试**

在测试数据库中给 `AAA` 插入两段不重叠 membership：

```python
connection.executemany(
    """
    INSERT INTO universe_memberships (
        universe_key, ticker, effective_from, effective_to,
        selection_rule, market_cap
    ) VALUES (?, ?, ?, ?, ?, ?)
    """,
    [
        ("research", "AAA", "2025-01-01", "2026-01-01", "fixture", 8_000_000_000),
        ("research", "AAA", "2026-01-01", None, "fixture", 12_000_000_000),
    ],
)
```

断言：

```python
snapshot = repository.snapshot("2026-07-24")
aaa = next(member for member in snapshot.members if member.ticker == "AAA")
self.assertEqual(aaa.market_cap, 12_000_000_000)
self.assertEqual(aaa.market_cap_asof, "2026-01-01")
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_research_universe
```

Expected: FAIL，`ResearchUniverseMember` 没有市值字段。

- [ ] **Step 3: 扩展 dataclass 与点时 SQL**

将 dataclass 增加：

```python
market_cap: float | None = None
market_cap_asof: str | None = None
```

将 `_MEMBER_METADATA_SQL` 改为按证券选取观察日有效且 `effective_from`
最大的 membership。继续沿用仓储当前“所有有效 membership 的并集”语义，不把
`universe_key` 硬编码为 `research`（真实数据库当前键为
`liquid_us_common_v1`）：

```sql
WITH ranked_memberships AS (
    SELECT ticker,
           market_cap,
           effective_from,
           ROW_NUMBER() OVER (
               PARTITION BY ticker
               ORDER BY effective_from DESC
           ) AS membership_rank
    FROM universe_memberships
    WHERE effective_from <= ?
      AND (effective_to IS NULL OR ? < effective_to)
)
SELECT membership.ticker AS ticker,
       security_master.name AS name,
       security_master.exchange AS exchange,
       membership.market_cap AS market_cap,
       membership.effective_from AS market_cap_asof
FROM ranked_memberships AS membership
LEFT JOIN security_master ON security_master.ticker = membership.ticker
WHERE membership.membership_rank = 1
ORDER BY membership.ticker
```

`_members_from_metadata` 只做安全数值转换，不分级：

```python
market_cap=(
    float(row["market_cap"])
    if row["market_cap"] is not None
    else None
),
market_cap_asof=iso_date(row["market_cap_asof"]),
```

- [ ] **Step 4: 运行仓储测试并确认 GREEN**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_research_universe
```

Expected: 所有测试通过，旧 fixture 没有市值时返回 `None`。

- [ ] **Step 5: 提交**

```bash
git add web/services/research_universe.py tests/test_web_research_universe.py
git commit -m "web: load point-in-time market caps"
```

---

### Task 3: Universe 与股票详情 API 合并

**Files:**
- Modify: `web/services/universe.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_universe_service.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `ResearchUniverseMember.market_cap`、`market_cap_asof` 和 `market_cap_fields`。
- Produces: universe row 与 `stock.summary` 中一致的三个市值字段。

- [ ] **Step 1: 写 universe 合并失败测试**

扩展 `FakeResearchUniverseRepository` 成员，让 `AAA` 同时属于主动池和研究目录、`BBB` 仅属于研究目录。断言：

```python
self.assertEqual(by_ticker["AAA"]["market_cap"], 250_000_000_000.0)
self.assertEqual(by_ticker["AAA"]["market_cap_tier"], "mega")
self.assertEqual(by_ticker["BBB"]["market_cap_tier"], "large")
self.assertEqual(by_ticker["SPY"]["market_cap_tier"], "unavailable")
```

- [ ] **Step 2: 运行 universe 测试并确认 RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_universe_service
```

Expected: FAIL，universe row 没有市值字段。

- [ ] **Step 3: 最小实现 universe 合并**

在 `build_universe_rows` 创建主动行时先加入：

```python
**market_cap_fields(None, None),
```

在研究重叠行和 `_research_only_row` 中覆盖：

```python
row.update(market_cap_fields(member.market_cap, member.market_cap_asof))
```

将 `UNIVERSE_ALGORITHM_VERSION` 递增，避免旧语义缓存继续命中。

- [ ] **Step 4: 写股票详情一致性失败测试**

针对主动股票和研究目录股票分别请求 `/api/stocks/<ticker>`，断言：

```python
self.assertEqual(response.json["summary"]["market_cap"], 250_000_000_000.0)
self.assertEqual(response.json["summary"]["market_cap_asof"], "2026-07-24")
self.assertEqual(response.json["summary"]["market_cap_tier"], "mega")
```

- [ ] **Step 5: 运行 API 测试并确认 RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_api
```

Expected: FAIL，详情 summary 尚未带市值。

- [ ] **Step 6: 将 universe row 市值注入详情 summary**

增加纯辅助函数：

```python
def _market_cap_for_ticker(universe_service, ticker):
    rows = universe_service.build().get("tickers", ())
    row = next(
        (value for value in rows if value.get("ticker") == ticker),
        {},
    )
    return {
        key: row.get(key)
        for key in ("market_cap", "market_cap_asof", "market_cap_tier")
    }
```

在主动和研究详情 payload 生成后统一：

```python
payload["summary"].update(
    _market_cap_for_ticker(universe_service, normalized_ticker)
)
```

该调用命中 `UniverseSnapshotService` 的 revision cache，不重新扫描外部数据。

- [ ] **Step 7: 运行后端相关测试并确认 GREEN**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest \
  tests.test_web_market_cap \
  tests.test_web_research_universe \
  tests.test_web_universe_service \
  tests.test_web_api
```

Expected: 所有测试通过。

- [ ] **Step 8: 提交**

```bash
git add web/services/universe.py web/app.py \
  tests/test_web_universe_service.py tests/test_web_api.py
git commit -m "web: expose market caps in stock APIs"
```

---

### Task 4: 双语市值标签与所选证券摘要

**Files:**
- Create: `web/static/js/market_cap.js`
- Modify: `web/static/js/universe.js`
- Modify: `web/static/js/app.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/dashboard.css`
- Modify: `web/templates/index.html`
- Modify: `tests/dashboard_runtime.mjs`
- Modify: `tests/test_web_assets.py`

**Interfaces:**
- Consumes: API 的 `market_cap`、`market_cap_asof`、`market_cap_tier`。
- Produces: `formatMarketCap(value)` 与 `marketCapLabel(row, locale)`，以及列表/详情 DOM。

- [ ] **Step 1: 写格式与本地化失败测试**

在 Node 测试中断言：

```javascript
assert.equal(formatMarketCap(4_890_000_000_000), "$4.89T");
assert.equal(formatMarketCap(674_900_000_000), "$674.9B");
assert.equal(formatMarketCap(850_000_000), "$850M");
assert.equal(formatMarketCap(null), null);
assert.equal(
  marketCapLabel(
    { market_cap: 674_900_000_000, market_cap_tier: "mega" },
    "zh-CN",
  ),
  "超大盘 · $674.9B",
);
assert.equal(
  marketCapLabel(
    { market_cap: null, market_cap_tier: "unavailable" },
    "en",
  ),
  "Market cap unavailable",
);
```

- [ ] **Step 2: 运行测试并确认 RED**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets
```

Expected: FAIL，`market_cap.js` 与 DOM 字段尚不存在。

- [ ] **Step 3: 实现金额格式与标签**

`market_cap.js` 导出：

```javascript
export function formatMarketCap(value) {
  if (!Number.isFinite(value) || value <= 0) return null;
  if (value >= 1e12) return `$${trim(value / 1e12, 2)}T`;
  if (value >= 1e9) return `$${trim(value / 1e9, 1)}B`;
  if (value >= 1e6) return `$${trim(value / 1e6, 0)}M`;
  if (value >= 1e3) return `$${trim(value / 1e3, 0)}K`;
  return `$${Math.round(value)}`;
}

export function marketCapLabel(row, locale) {
  const amount = formatMarketCap(row?.market_cap);
  const tier = row?.market_cap_tier || "unavailable";
  if (!amount || tier === "unavailable") {
    return t("marketCap.unavailable", {}, locale);
  }
  return t(
    "marketCap.label",
    { tier: t(`marketCap.tier.${tier}`, {}, locale), amount },
    locale,
  );
}
```

其中 `trim(value, digits)` 用 `toFixed` 后去除无意义尾零。

- [ ] **Step 4: 渲染列表与详情**

在 `renderUniverse` 的 `ticker-meta` 中加入：

```javascript
const cap = appendText(
  metadata,
  "ticker-market-cap",
  marketCapLabel(row, locale),
);
cap.dataset.tier = row.market_cap_tier || "unavailable";
cap.title = marketCapAccessibleLabel(row, locale);
```

模板的 `quote-grid` 增加：

```html
<div>
  <dt data-i18n="security.marketCap">市值</dt>
  <dd id="selected-market-cap">—</dd>
</div>
```

`captureElements` 捕获 `selectedMarketCap`；`renderStockHeader` 使用 `payload.summary`，并将完整市值日期写入 `title` 和 `aria-label`。

- [ ] **Step 5: 增加双语文案与样式**

新增中文/英文键：

```text
security.marketCap
marketCap.label
marketCap.unavailable
marketCap.tier.mega
marketCap.tier.large
marketCap.tier.mid
marketCap.tier.small
marketCap.tier.micro
marketCap.asof
```

列表标签保持单行，等级使用文字和 `data-tier` 色调；`unavailable` 使用中性色。

- [ ] **Step 6: 扩展运行时测试**

fixture 的 AAA 使用 `250B / mega / 2026-07-24`。断言中文列表和详情包含 `超大盘 · $250B`，切换英文后包含 `Mega cap · $250B`，缺失 fixture 显示不可用且不含 `$0`。

- [ ] **Step 7: 运行前端测试并确认 GREEN**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest tests.test_web_assets
```

Expected: 所有测试通过。

- [ ] **Step 8: 提交**

```bash
git add web/static/js/market_cap.js web/static/js/universe.js \
  web/static/js/app.js web/static/js/i18n.js web/static/css/dashboard.css \
  web/templates/index.html tests/dashboard_runtime.mjs tests/test_web_assets.py
git commit -m "web: render bilingual market cap badges"
```

---

### Task 5: 全量回归与真实数据验收

**Files:**
- No production file changes expected.

**Interfaces:**
- Consumes: Tasks 1–4 的完整实现。
- Produces: 可合并、可在本地服务验收的功能分支。

- [ ] **Step 1: 运行完整测试套件**

Run:

```bash
/Users/renyinghao.1/Project/stock_screener/venv/bin/python -m unittest discover -s tests
```

Expected: 0 failures。

- [ ] **Step 2: 检查代码与迁移安全**

Run:

```bash
git diff --check
git status --short
```

Expected: 无空白错误，只包含本计划文件。

- [ ] **Step 3: 用真实研究目录核对样本**

对 `/api/universe` 检查：

- AAPL 显示 `mega` 且约 `$4.89T`；
- ASML 显示 `mega` 且约 `$674.9B`；
- CCEP 显示 `large` 且约 `$46.5B`；
- 缺失证券显示 `unavailable`，不显示 0。

- [ ] **Step 4: 浏览器验收**

在 `http://127.0.0.1:5000/` 验证：

- 列表市值标签不挤压股票代码、门槛和形态；
- 切换中文/英文只改变文案，不重新请求股票数据；
- 选择不同股票时右侧市值同步切换；
- 缺失市值显示明确不可用；
- 页面无运行时错误。

- [ ] **Step 5: 完成分支**

使用 `superpowers:verification-before-completion` 获取新鲜证据，再使用 `superpowers:finishing-a-development-branch` 选择合并、PR 或保留分支。
