# 退市证券类型净化与身份键 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 EODHD 退市目录转换为可审计的普通股回填候选、拒绝项和人工复核项，并为有效 ISIN 建立稳定身份键。

**Architecture:** `data/delisted_security_catalog.py` 只实现确定性纯函数；命令行运行器负责文件哈希、原子输出和审计报告。完整净化目录保存在忽略的缓存目录，Git 只提交代码、测试和汇总证据。

**Tech Stack:** Python 3.9、dataclasses、JSON、hashlib、unittest。

## Global Constraints

- 规则版本固定为 `delisted_security_purification_v1`。
- 只有 `accepted_common` 可以设置 `backfill_eligible=true`。
- 无有效 ISIN 时不得生成稳定身份键，不得自动拼接价格序列。
- 交易首末日期不得解释为指数成员区间。
- 原始完整目录与净化明细不得进入 Git。

---

### Task 1: 单行类型判断与 ISIN 身份

**Files:**
- Create: `data/delisted_security_catalog.py`
- Create: `tests/test_delisted_security_catalog.py`

**Interfaces:**
- Produces: `classify_catalog_row(raw: Mapping) -> dict`
- Produces: `valid_isin(value: object) -> bool`

- [x] **Step 1: 写失败测试**

覆盖普通股、SPAC 普通股、warrant/unit/right/preferred/debt/fund、空名称、
非法代码、范围外交易所、有效/缺失/非法 ISIN。断言稳定状态、原因码、
`identity_key` 和 `backfill_eligible`。

- [x] **Step 2: 运行测试确认缺少接口**

Run:
`PYTHONWARNINGS=error ../../venv/bin/python -m unittest tests.test_delisted_security_catalog -q`

Expected: FAIL，无法导入 `data.delisted_security_catalog`。

- [x] **Step 3: 实现最小纯函数**

使用预编译正则、ISO 6166 字符展开和 Luhn 校验。范围门先于三态判断；拒绝
信号先于歧义信号。返回字段固定为：

```python
{
    "ticker": "AAA",
    "name": "AAA Inc",
    "exchange": "NASDAQ",
    "currency": "USD",
    "provider_type": "Common Stock",
    "provider_isin": "US0000000002",
    "scope_status": "in_scope",
    "classification": "accepted_common",
    "reason_codes": [],
    "evidence": [],
    "identity_status": "strong_isin",
    "identity_key": "isin:US0000000002",
    "backfill_eligible": True,
    "rule_version": "delisted_security_purification_v1",
}
```

- [x] **Step 4: 重跑目标测试**

Expected: PASS，无 warning。

- [x] **Step 5: 提交**

```bash
git add data/delisted_security_catalog.py tests/test_delisted_security_catalog.py
git commit -m "data: classify delisted security types"
```

### Task 2: 全目录冲突处理与汇总

**Files:**
- Modify: `data/delisted_security_catalog.py`
- Modify: `tests/test_delisted_security_catalog.py`

**Interfaces:**
- Consumes: `classify_catalog_row(raw)`
- Produces: `build_delisted_catalog(rows: Sequence[Mapping]) -> dict`
- Produces: `summarize_delisted_catalog(catalog: Mapping) -> dict`

- [x] **Step 1: 写失败测试**

以手工目录验证输入顺序不影响输出、范围内重复代码失败、同一 ISIN 的名称或
分类冲突会把相关行都改为 `needs_review/identity_conflict`，以及交易所、
分类、原因和身份覆盖统计。

- [x] **Step 2: 运行测试确认新接口缺失**

Expected: FAIL，仅因为 `build_delisted_catalog` / `summarize_delisted_catalog`
尚不存在。

- [x] **Step 3: 实现目录构建**

输出 `schema_version=delisted_security_catalog_v1`、规则版本、按
`(exchange,ticker)` 排序的 `securities`。冲突处理不得删除行；原因码去重
排序，并同步把 `backfill_eligible` 改为 false。

- [x] **Step 4: 实现确定性汇总**

汇总输入数、范围内数、四种分类数、各交易所分类数、原因计数、身份状态计数和
每种原因最多五个按代码排序的样例。

- [x] **Step 5: 重跑目标测试并提交**

```bash
git add data/delisted_security_catalog.py tests/test_delisted_security_catalog.py
git commit -m "data: audit delisted catalog conflicts"
```

### Task 3: 原子运行器与审计报告

**Files:**
- Create: `build_delisted_security_catalog.py`
- Create: `tests/test_build_delisted_security_catalog.py`

**Interfaces:**
- Consumes: `build_delisted_catalog`, `summarize_delisted_catalog`
- Produces: `build_catalog_files(input_path, output_catalog, manifest_path, report_json, report_markdown, observed_at) -> dict`

- [ ] **Step 1: 写失败测试**

临时目录写五行 fixture，验证净化目录、manifest、JSON/Markdown 报告、输入和
输出 SHA-256、字节稳定目录、原子替换后无 `.tmp`，且任意传入的假 token
不会出现在产物。

- [ ] **Step 2: 运行测试确认接口缺失**

Expected: FAIL，无法导入运行器。

- [ ] **Step 3: 实现运行器**

运行时间只写 manifest；净化目录不含时间戳。报告 JSON 只含 schema、规则、
哈希和汇总，不复制完整 securities。Markdown 用中文表格展示分类、原因和
身份覆盖，并明确“不是指数成员区间”。

- [ ] **Step 4: 重跑目标与相关测试**

Run:
`PYTHONWARNINGS=error ../../venv/bin/python -m unittest tests.test_build_delisted_security_catalog tests.test_delisted_security_catalog tests.test_delisted_history_pilot -q`

- [ ] **Step 5: 提交**

```bash
git add build_delisted_security_catalog.py tests/test_build_delisted_security_catalog.py
git commit -m "data: build purified delisted catalog"
```

### Task 4: 真实目录审计、TODO 与合并

**Files:**
- Create: `reports/delisted-security-purification.json`
- Create: `reports/delisted-security-purification.md`
- Modify: `docs/modeling-todo.md`
- Modify: `docs/superpowers/plans/2026-07-27-delisted-security-purification.md`

- [ ] **Step 1: 运行真实 32,371 行目录**

完整净化目录写到
`data/cache/eodhd_delisted_security_catalog/2026-07-27/catalog.json`，manifest
同目录保存；汇总写入 `reports/`。

- [ ] **Step 2: 验证真实结果**

检查输入行数、状态总和、试点 24 个明显误分类代码均不再 eligible、哈希、
无临时文件、无密钥、缓存被 Git 忽略。

- [ ] **Step 3: 更新中文 TODO**

关闭“证券类型二次净化和身份契约”部分；保留正式退市日线下载、SEC 历史行业和
历史成员区间为未完成。

- [ ] **Step 4: 全量验证**

Run:
`LOKY_MAX_CPU_COUNT=8 PYTHONWARNINGS=error ../../venv/bin/python -m unittest discover -s tests -q`

随后运行 `git diff --check`、报告不变量与密钥扫描。

- [ ] **Step 5: 合入 main 并复验**

非快进合并，保留主目录现有用户文件；合并后再次运行全量测试并记录结果。
