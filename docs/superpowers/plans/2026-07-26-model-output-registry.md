# 模型输出注册接口实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立向后兼容、可验证的模型输出注册接口，使后端按注册定义构建输出，前端无需硬编码即可渲染新增模型组。

**Architecture:** 新增纯 Python 注册表保存组、模型和构建器，并生成公开 JSON 契约；现有模型输出函数作为构建器接入默认注册表。API 保留旧分组字段并附加 `registry`，前端优先按注册契约动态渲染，旧 payload 回退到原四组。

**Tech Stack:** Python 3.9、dataclasses、Flask JSON 合约、原生 ES modules、Node 运行时 DOM 测试、unittest。

## Global Constraints

- 不改变现有模型数值、状态、阈值或 `forecast_decision_policy` 结论。
- 保留 `primary`、`downside`、`bullish_structure` 和 `decision` 旧字段。
- 旧缓存没有 `registry` 时必须正常显示。
- 注册身份字段不能被模型构建器覆盖。
- 规则分数继续明确标注“不是概率”。
- UI 改动不得创建图表价格线、标记或影响日期锁定、缩放和拖拽。

---

### Task 1: 建立模型输出注册表核心

**Files:**
- Create: `web/forecasts/model_output_registry.py`
- Create: `tests/test_web_model_output_registry.py`

**Interfaces:**
- Consumes: `collections.abc.Callable`, `collections.abc.Mapping`。
- Produces: `ModelOutputGroup`, `ModelOutputDefinition`, `ModelOutputRegistry.register_group()`, `ModelOutputRegistry.register_model()`, `ModelOutputRegistry.build()`, `ModelOutputRegistry.public_contract()`。

- [ ] **Step 1: 写失败测试，定义注册、排序和输出契约**

```python
def test_registry_builds_sorted_groups_and_models():
    registry = ModelOutputRegistry()
    registry.register_group(ModelOutputGroup(
        key="risk", label_key="group.risk", order=20, cardinality="many"
    ))
    registry.register_group(ModelOutputGroup(
        key="primary", label_key="group.primary", order=10, cardinality="many"
    ))
    registry.register_model(
        ModelOutputDefinition(
            key="later", group="risk", order=20, version="v1",
            kind="rule_score", lifecycle="production",
            timing="close_confirmed", decision_permission="advisory",
            name_key="model.later.name",
            explanation_key="model.later.explanation",
            limitation_key="model.later.limitation",
        ),
        lambda context: {"status": "active", "score": 42},
    )
    outputs = registry.build({})
    assert outputs["risk"][0]["key"] == "later"
    assert outputs["risk"][0]["decision_permission"] == "advisory"
    assert outputs["registry"]["groups"][0]["key"] == "primary"
```

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `../../venv/bin/python -m unittest tests.test_web_model_output_registry -v`

Expected: FAIL with `ModuleNotFoundError: web.forecasts.model_output_registry`。

- [ ] **Step 3: 实现不可变定义、注册验证和构建**

注册表必须：

```python
VALID_CARDINALITIES = frozenset({"many", "single"})
VALID_DECISION_PERMISSIONS = frozenset({
    "informational", "advisory", "downgrade_to_neutral",
    "veto_to_down", "final_policy",
})
REGISTRY_VERSION = "model_output_registry_v1"
```

`register_group()` 拒绝重复键和非法基数；`register_model()` 拒绝重复模型、未知组和非法权限；`build()` 按组与模型 `order/key` 稳定排序，将注册身份字段与构建器结果合并，并拒绝构建器返回非 Mapping 或覆盖 `key/group/order/decision_permission`。

- [ ] **Step 4: 增加失败路径测试**

分别断言重复组、重复模型、未知组、非法权限、构建器非 Mapping 和身份覆盖均抛出 `ValueError` 或 `TypeError`；单模型组输出对象，多模型组输出数组。

- [ ] **Step 5: 运行注册表测试**

Run: `../../venv/bin/python -m unittest tests.test_web_model_output_registry -v`

Expected: PASS。

- [ ] **Step 6: 提交注册表核心**

```bash
git add web/forecasts/model_output_registry.py tests/test_web_model_output_registry.py
git commit -m "feat: add model output registry"
```

---

### Task 2: 将现有模型输出接入默认注册表

**Files:**
- Modify: `web/forecasts/model_outputs.py`
- Modify: `tests/test_web_model_outputs.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: Task 1 的 `ModelOutputRegistry` 和定义类。
- Produces: `default_model_output_registry()`；`build_model_outputs()` 返回旧字段加 `registry`，每个输出带 `group/order/decision_permission`。

- [ ] **Step 1: 写失败测试，要求默认模型全部注册**

```python
def test_default_registry_describes_every_output():
    outputs = build_model_outputs(forecast_payload(), chart_row(), {})
    registered = {item["key"] for item in outputs["registry"]["models"]}
    emitted = {
        item["key"]
        for group in ("primary", "downside", "bullish_structure")
        for item in outputs[group]
    } | {outputs["decision"]["key"]}
    assert registered == emitted
    assert outputs["decision"]["decision_permission"] == "final_policy"
```

同时更新原字段集合断言，加入 `registry`。

- [ ] **Step 2: 运行测试并确认缺少注册契约而失败**

Run: `../../venv/bin/python -m unittest tests.test_web_model_outputs -v`

Expected: FAIL because `registry` is missing。

- [ ] **Step 3: 装配默认组和模型**

在 `model_outputs.py` 中新增 `_default_registry()`，注册：

- `primary`：Ridge；
- `downside`：即时风险、个股记忆、板块风险、持续阴跌、高位派发、宏观计划、盘中计划；
- `bullish_structure`：结构转强、早期观察、严格 VCP、紧密平台、VCP 突破、Pocket Pivot、需求计划；
- `decision`：最终策略，`cardinality="single"`。

构建器使用闭包从上下文调用现有纯函数。`build_model_outputs()` 只规范化输入并调用默认注册表。

- [ ] **Step 4: 为每个模型配置明确权限**

使用：

- Ridge、VCP、Pocket Pivot、结构转强：`informational`；
- 早期观察、板块风险、需求、宏观、盘中：`advisory`；
- 持续阴跌和高位派发：`downgrade_to_neutral`；
- 8 项即时向下确认和 12 项个股记忆风险：`veto_to_down`；
- `forecast_decision_policy`：`final_policy`。

- [ ] **Step 5: 运行模型输出和 API 合约测试**

Run: `../../venv/bin/python -m unittest tests.test_web_model_outputs tests.test_web_api.WebApiTest.test_model_outputs_attach_same_date_chart_evidence -v`

Expected: PASS。

- [ ] **Step 6: 提交默认注册表接入**

```bash
git add web/forecasts/model_outputs.py tests/test_web_model_outputs.py tests/test_web_api.py
git commit -m "feat: register dashboard model outputs"
```

---

### Task 3: 前端动态分组、双语权限与 TODO 收尾

**Files:**
- Modify: `web/static/js/model_outputs.js`
- Modify: `web/static/js/i18n.js`
- Modify: `tests/model_outputs_runtime.mjs`
- Modify: `tests/test_web_assets.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: `outputs.registry.groups`、模型的 `decision_permission`。
- Produces: 注册驱动分组渲染、旧 payload 回退和双语权限标签。

- [ ] **Step 1: 写失败的 Node 运行时用例**

在 fixture 的 `registry.groups` 增加前端从未硬编码的：

```javascript
{
  key: "macro_context",
  label_key: "modelOutput.group.macro",
  order: 35,
  cardinality: "many",
}
```

并增加 `macro_context` 模型数组。断言中文文本包含“宏观环境”和模型名称，卡片数增加；删除 `registry` 后再次渲染并断言旧四组仍显示。

- [ ] **Step 2: 运行前端测试并确认新组未显示**

Run: `../../venv/bin/python -m unittest tests.test_web_assets.WebAssetTest.test_model_output_renderer_is_bilingual_and_explicit_about_scores -v`

Expected: FAIL because the unknown registered group is not rendered。

- [ ] **Step 3: 实现注册驱动分组与安全回退**

移除固定 `GROUPS` 作为主路径，新增：

```javascript
function outputGroups(outputs) {
  const registered = outputs?.registry?.groups;
  if (validRegisteredGroups(registered)) {
    return [...registered].sort((a, b) => a.order - b.order);
  }
  return LEGACY_GROUPS;
}
```

统一处理 `many` 和 `single`，组标题读取 `label_key`。摘要条继续从旧字段读取以保持兼容。

- [ ] **Step 4: 展示决策权限并补齐中英文**

新增 `modelOutput.field.decisionPermission` 和五个 `modelOutput.permission.*` 翻译。卡片身份区域下方增加决策权限字段，缺失时显示“—”。

- [ ] **Step 5: 更新全局 TODO**

将索引中的 `UI-002` 改为“已完成”，勾选模型注册接口；在 DATA-001 看板分类完成项后注明主题板块摘要已始终返回真实相对收益、风险和覆盖率，IGV/XSW 正式行情覆盖已补齐。

- [ ] **Step 6: 运行相关测试和全量测试**

Run:

```bash
../../venv/bin/python -m unittest tests.test_web_assets tests.test_web_model_outputs tests.test_web_model_output_registry -v
../../venv/bin/python -m unittest discover -s tests
node --check web/static/js/model_outputs.js
git diff --check
```

Expected: all PASS，JavaScript syntax valid，no whitespace errors。

- [ ] **Step 7: 提交 UI 和文档**

```bash
git add web/static/js/model_outputs.js web/static/js/i18n.js \
  tests/model_outputs_runtime.mjs tests/test_web_assets.py docs/modeling-todo.md
git commit -m "feat: render registered model output groups"
```
