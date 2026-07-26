# 市场压力阶段向下风险专家 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立使用次日开盘路径 MAE 标签的压力阶段二分类专家，并完成同池、分组和分阶段走步评估。

**Architecture:** 独立 `research/downside_specialist.py` 负责标签、压力阶段门控、走步训练与二分类指标；独立运行器复用扩展研究数据和现有 Ridge/通用 Logistic 预测，生成报告但不接线上策略。

**Tech Stack:** Python 3.9、pandas、NumPy、scikit-learn、unittest、SQLite 研究库。

## Global Constraints

- 信号在观察日收盘后生成，下一交易日开盘执行。
- 五日/二十日路径风险阈值固定为 −5%/−10%。
- 仅在市场承压、修正和急跌阶段训练与输出。
- 标签结束日必须严格早于测试折首日。
- 不使用股票代码独热特征，不修改线上模型。

---

### Task 1: 路径 MAE 标签

**Files:**
- Create: `research/downside_specialist.py`
- Create: `tests/test_downside_specialist.py`

**Interfaces:**
- Produces: `attach_next_open_mae_targets(frame, histories, horizons=(5, 20)) -> DataFrame`。

- [x] 写失败测试，覆盖次日开盘、未来最低价、终点反弹、缺失路径和标签结束日。
- [x] 运行 `../../venv/bin/python -m unittest tests.test_downside_specialist -v`，确认因接口缺失失败。
- [x] 实现最小标签构建器。
- [x] 重跑测试，确认标签测试通过。
- [x] 提交 `research: add executable downside path labels`。

### Task 2: 压力阶段走步专家

**Files:**
- Modify: `research/downside_specialist.py`
- Modify: `tests/test_downside_specialist.py`

**Interfaces:**
- Produces: `walk_forward_downside_predictions(frame, horizon, feature_columns, n_folds, minimum_samples) -> DataFrame`。

- [x] 写失败测试，覆盖阶段门控、精确标签禁入、训练折预处理、分数范围和追加未来不改变旧预测。
- [x] 运行目标测试并确认缺少训练接口。
- [x] 实现类别平衡 Logistic、固定 `C=0.1`、稳定 sigmoid 和 0.5 阈值。
- [x] 重跑测试并确认通过。
- [x] 提交 `research: add pressure-regime downside specialist`。

### Task 3: 同池比较与晋级门槛

**Files:**
- Modify: `research/downside_specialist.py`
- Modify: `tests/test_downside_specialist.py`

**Interfaces:**
- Produces: `evaluate_downside_predictions(predictions, minimum_fold_samples=30) -> DataFrame` 和 `downside_promotion_decision(metrics) -> dict`。

- [x] 写失败测试，覆盖精确率、召回率、特异度、BA、ROC/PR AUC、Brier、非重叠样本和稀疏折排除。
- [x] 运行目标测试并确认接口缺失。
- [x] 实现指标、同池连接和冻结晋级门槛。
- [x] 运行相关测试并确认通过。
- [x] 提交 `research: evaluate downside specialist fairly`。

### Task 4: 真实实验与报告

**Files:**
- Create: `research/run_pressure_downside_study.py`
- Create: `tests/test_run_pressure_downside_study.py`
- Create: `reports/pressure-downside-specialist.csv`
- Create: `reports/pressure-downside-specialist.md`
- Create: `reports/pressure-downside-specialist.json`
- Modify after merge: `docs/modeling-todo.md`

**Interfaces:**
- 运行器加载 240 股票及 QQQ/SPY，复用冻结队列、特征和市场阶段。

- [x] 写失败测试，覆盖报告清单、中文结论、失败原因和输出完整性。
- [x] 实现运行器与报告渲染。
- [x] 运行 240 股票、2018-01-01 起点、五折真实实验。
- [x] 检查所有组、阶段、周期和样本模式，冻结结论。
- [x] 运行 `../../venv/bin/python -m unittest discover -s tests -q`。
- [x] 提交报告，合入 main 后无覆盖地更新中文 TODO。
