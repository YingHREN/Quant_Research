# 模型输出注册接口设计

## 目标

为统一模型输出决策面板建立版本化注册接口，使供给、需求、宏观和盘中模型能够通过注册元数据与输出构建器进入 API 和 UI，而无需再次修改图表核心分组代码。

本设计只负责“模型如何声明并呈现”，不改变任何现有模型数值、方向、风险阈值或决策策略。

## 当前问题

`web/forecasts/model_outputs.py` 直接构造四个固定字段，`web/static/js/model_outputs.js` 又用固定 `GROUPS` 数组渲染前三组，并单独硬编码决策组。新增模型时必须同步修改后端列表和前端分组，容易造成：

- API 已有模型但 UI 未展示；
- 模型顺序在前后端不一致；
- 生命周期、时点语义和决策权限缺失或写法不一致；
- 新增组时必须改动图表核心渲染逻辑。

## 方案选择

### 方案 A：只在 API 增加静态元数据

改动最小，但后端模型构建和前端分组仍然硬编码，不能解决扩展成本问题。

### 方案 B：注册表驱动后端构建与前端分组（采用）

建立独立注册表，保存组定义、模型定义和模型输出构建器。API 保留旧字段并新增公开注册契约；UI 根据契约动态分组、排序和展示。该方案兼容旧缓存，同时让未来模型只需注册并提供构建器。

### 方案 C：彻底改成统一模型数组

数据结构最整齐，但会破坏现有 API、缓存和测试，迁移收益不足以抵消风险。

## 后端架构

新增 `web/forecasts/model_output_registry.py`，包含：

- `ModelOutputGroup`：组键、翻译键、顺序和基数；
- `ModelOutputDefinition`：模型键、所属组、顺序、默认版本、类型、生命周期、时点语义、决策权限及翻译键；
- `ModelOutputRegistry`：注册组、注册模型、拒绝重复或非法定义、按顺序构建输出及生成 JSON 安全的公开契约。

模型构建器接收只读上下文：

```python
{
    "forecast": Mapping,
    "chart_row": Mapping,
    "evaluation": Mapping,
    "decision": Mapping,
}
```

构建器只返回模型当日状态和数值。注册表将定义中的身份元数据合并到输出，并拒绝构建器篡改模型键、分组或决策权限。

默认注册表仍由 `web/forecasts/model_outputs.py` 装配，复用现有纯函数。`build_model_outputs()` 的旧调用方式不变。

## API 契约

现有单次构建函数输出保持不变：

```json
{
  "primary": [],
  "downside": [],
  "bullish_structure": [],
  "decision": {}
}
```

新增注册目录。批量股票 API 将该目录提升为响应顶层
`model_output_registry`，每个日期的 `model_outputs` 只保留
`registry_ref: "model_output_registry_v1"`，避免在数百个历史日期和多个
预测周期中重复发送同一份元数据。单次纯函数构建仍可返回完整 `registry`，
便于独立测试和非 HTTP 消费者使用。

注册目录结构：

```json
{
  "registry": {
    "version": "model_output_registry_v1",
    "groups": [
      {
        "key": "primary",
        "label_key": "modelOutput.group.primary",
        "order": 10,
        "cardinality": "many"
      }
    ],
    "models": [
      {
        "key": "ridge_direction_v1",
        "group": "primary",
        "order": 10,
        "kind": "statistical_forecast",
        "lifecycle": "production",
        "timing": "next_session_open",
        "decision_permission": "informational"
      }
    ]
  }
}
```

每个模型输出同时包含 `group`、`order` 和 `decision_permission`，方便日志、导出和非网页消费者独立理解。

决策权限使用封闭枚举：

- `informational`：仅提供原始预测或形态信息；
- `advisory`：可提供风险或机会提示，但不能覆盖方向；
- `downgrade_to_neutral`：最多将上涨降级为中性；
- `veto_to_down`：满足模型自身确认条件后可参与向下否决；
- `final_policy`：汇总其他模型并输出最终方向。

权限描述的是策略允许范围，不代表模型当日一定触发。

## 前端行为

`model_outputs.js` 按以下顺序选择分组：

1. 优先使用调用方传入的顶层 `model_output_registry.groups`；
2. 若独立输出自带 `outputs.registry.groups`，则使用内嵌目录；
3. 若注册契约缺失或损坏，则回退到当前四组，兼容旧预测缓存；
4. `cardinality=many` 读取数组，`cardinality=single` 将对象包装为单卡片；
5. 未知新组使用注册的 `label_key`，无需修改固定 `GROUPS`；
6. 每张卡展示决策权限，继续展示类型、生命周期和时点语义。

注册表只控制固定尺寸面板内部内容，不创建价格线、标记或覆盖层，因此不会影响 K 线缩放、拖拽、价格轴或日期锁定。

## 错误处理与兼容性

- 重复组、重复模型、未知组、非法基数和非法决策权限在应用启动或测试时立即报错；
- 单个模型构建器返回非映射值时立即报错，不静默生成空模型；
- 旧 payload 没有 `registry` 时保持旧 UI；
- 批量响应只发送一份顶层目录；每日期引用必须与目录版本一致；
- 计划模型继续输出 `unavailable/not_implemented`，不得伪造分数；
- 注册元数据不得覆盖构建器提供的当日状态、分数、证据和不可用原因；
- 构建器不得覆盖注册身份字段。

## 测试与验收

后端测试覆盖：

- 注册、排序和动态新增模型；
- 重复键、未知组和非法权限；
- 默认注册表包含全部现有模型；
- 旧输出字段和值保持兼容；
- 每个模型带分组、顺序和决策权限；
- 公开契约可 JSON 序列化且不暴露 Python 构建器。

前端运行时测试覆盖：

- 注册表驱动当前四组；
- 注入一个从未写进前端常量的新组后仍能显示；
- 注册表缺失时旧 payload 正常回退；
- 单模型组与多模型组均能渲染；
- 决策权限中文和英文均能显示。

验收标准：

- 后续模型只需注册组或模型并提供输出构建器，不需要改 `model_outputs.js` 的分组核心；
- 现有 API 消费者和旧缓存继续工作；
- 模型数值、最终决策和图表交互无回归；
- 全量测试通过后更新 `UI-002` 为已完成。
