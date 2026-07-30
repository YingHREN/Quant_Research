# SPY/QQQ 政策时期色带长期图实施计划

**Goal:** 在市场页增加只读的 SPY/QQQ 长期价格图与政策时期色带，点击任意时期
后复用政策矩阵的官方事件详情，同时保证色带不改变价格轴、可见范围或拖拽行为。

**Architecture:** 新增轻量只读 `policy-benchmark-history` 服务，只返回 SPY/QQQ
复权收盘历史，不重复计算宏观风险。前端用 Lightweight Charts 绘制价格线，
政策色带作为绝对定位覆盖层，横坐标由 `timeToCoordinate()` 映射；覆盖层本身
`pointer-events:none`，点击由图表时间回调解析对应时期，因此不会拦截拖拽。
政策时期继续来自 `policy_period_matrix_v1`，不建立第二套目录。

**Constraints:**

- 任务编号继续为 `MACRO-ROTATION-001`。
- 只支持 `SPY`、`QQQ`；默认 `SPY`。
- 历史价格使用分红复权口径，优先研究价格库，缺失时才使用主价格库。
- 请求时点之后的价格和 `available_at` 之后才可见的时期不得返回。
- 进行中时期延伸到 `asof`，但必须标记“进行中”。
- 色带不得进入价格序列、自动缩放或数据范围计算。
- 色带不得捕获鼠标事件；拖拽、缩放和宏观日期锁定保持原行为。
- 输出固定为历史描述，`research/advisory/none`，不改 Ridge、板块分数或否决。
- 页面读取不得下载行情、导入政策目录、创建缺失数据库或写缓存文件。

---

### Task 1: 建立轻量只读基准历史服务

**Files:**
- Create: `web/services/policy_benchmark_history.py`
- Create: `tests/test_web_policy_benchmark_history.py`

- [ ] 写失败测试，冻结 `SPY/QQQ` 白名单、点时截止、复权收盘、JSON 安全字段
  和 `research/advisory/none` 权限。
- [ ] 写失败测试，覆盖研究价格库优先、主价格库降级、两库均缺失、非法代码及
  有界缓存深拷贝。
- [ ] 实现 `PolicyBenchmarkHistoryService.build(asof, benchmark)`，输出：

```text
artifact_key=policy_benchmark_history_v1
asof
benchmark
rows[{time, close, normalized}]
point_in_time=true
historical_description_only=true
lifecycle=research
decision_permission=advisory
online_authority=none
unavailable_reason
```

- [ ] 缓存键包含两个价格库 token、`asof` 和 benchmark；读取不得创建数据库。
- [ ] 运行聚焦测试并提交 `feat: serve policy benchmark history`。

### Task 2: 增加独立 API

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_api.py`
- Modify: `web/static/js/api.js`

- [ ] 写失败 API 测试：
  - `GET /api/policy-benchmark-history?benchmark=SPY`
  - `QQQ` 可用；
  - 其他 ticker 返回稳定 400；
  - 缺库仍返回 200 typed-unavailable，且不创建文件。
- [ ] 从可选 `POLICY_BENCHMARK_HISTORY_SERVICE` 注入；默认复用主价格仓库与
  `RESEARCH_DATABASE`。
- [ ] 前端只增加 `getPolicyBenchmarkHistory()`，不得调用宏观历史接口代替。
- [ ] 运行 `tests.test_web_api` 和服务测试并提交
  `feat: expose policy benchmark history api`。

### Task 3: 实现不影响坐标轴的政策色带图

**Files:**
- Create: `web/static/js/policy-period-chart.mjs`
- Create: `tests/policy_period_chart_runtime.mjs`

- [ ] 先写纯函数失败测试：
  - `policyBandSegments(periods, rows, asof)` 正确裁剪起止日期；
  - 未来时期不可见；
  - 进行中时期延伸至 `asof`；
  - 日期空档不伪造价格；
  - 点击日期解析唯一时期。
- [ ] 冻结图表契约：
  - 价格只进入一条 LineSeries；
  - 色带只进入 `.policy-band-overlay`；
  - overlay 与 band 均 `pointer-events:none`；
  - 切换 benchmark 更新同一图表，不创建叠加实例；
  - resize 只更新宽度与色带坐标，不改变固定高度。
- [ ] 图表点击回调只返回 `period_id`；由市场页更新已有时期详情。
- [ ] 运行 Node 运行时与语法测试并提交
  `feat: render non-scaling policy period bands`。

### Task 4: 接入双语市场页并联动时期详情

**Files:**
- Modify: `web/templates/market.html`
- Modify: `web/static/js/market.js`
- Modify: `web/static/js/i18n.js`
- Modify: `web/static/css/market.css`
- Modify: `tests/test_web_market_assets.py`

- [ ] 在政策矩阵标题与指标按钮之间加入：
  - `#policy-period-chart`
  - `SPY/QQQ` 切换
  - 加载/缺失状态
  - “色带是历史分段，不是预测”说明。
- [ ] `state.policyBenchmark` 默认 `SPY`；切换 benchmark 才请求轻量接口。
- [ ] 图表点击时期后设置 `state.policyPeriodId`，调用现有
  `renderPolicyPeriodMatrix()` 更新详情；不重新请求市场概览。
- [ ] 中文、英文切换保留 benchmark、指标与所选时期。
- [ ] CSS 固定图表高度，窄屏使用容器宽度；色带覆盖层不扩大页面宽度。
- [ ] 运行资产、Node 与语法测试并提交
  `feat: add policy band long chart`。

### Task 5: 真实验收、文档与合并

**Files:**
- Modify: `docs/dashboard.md`
- Modify: `docs/modeling-todo.md`
- Create: `reports/policy-band-chart-audit.md`
- Create: `reports/policy-band-chart-audit.json`

- [ ] 用真实本地库检查 SPY/QQQ 覆盖起止、行数、三个时期裁剪和权限。
- [ ] 聚焦测试覆盖服务、API、图表纯函数、运行时、双语和缺失降级。
- [ ] 运行完整测试套件。
- [ ] 浏览器验收：
  - 宽屏无页面横向溢出；
  - SPY/QQQ 切换只触发一次轻量请求；
  - 点击色带能更新时期详情；
  - 拖拽和缩放不被色带拦截；
  - 指标、语言和时期切换不改变图表固定高度；
  - 宏观历史日期锁定保持独立；
  - 页面明确显示历史描述与无线上决策权。
- [ ] 只勾选 TODO 中政策色带与详情联动；历史类比、切片器、RRG、轮动状态机
  和走步验证继续保持未完成。
- [ ] 合并到 `main`，重启服务并验证真实 API。

---

## 后续边界

色带长期图完成后，再进入连续宏观状态向量与板块历史条件先验。历史相似月份
必须等连续变量覆盖、禁入期和最小样本门槛冻结后才能实现，不从三个人工政策
时期直接推导板块推荐。
