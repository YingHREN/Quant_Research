# 市场阶段分层走步研究 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为现有扩展走步预测增加因果市场阶段标注和分层诊断报告。

**Architecture:** 独立模块从 QQQ/SPY 生成版本化市场阶段；现有走步脚本只消费阶段表并分层评估预测，不改变特征、标签、模型或生产晋级决策。报告保存为独立文件并明确稀疏证据。

**Tech Stack:** Python 3.9、pandas、NumPy、scikit-learn、unittest、SQLite 研究库。

## Global Constraints

- 只使用观察日及此前可见数据。
- 固定 `market_regime_v1` 阈值后再运行真实实验。
- 继续使用次日开盘进入与标签禁入期。
- 不修改线上模型、网页或最终方向。
- 不把稀疏阶段或缺失状态合并进总体结论。

---

### Task 1: 因果市场阶段状态机

**Files:**
- Create: `research/market_regime.py`
- Create: `tests/test_market_regime.py`

**Interfaces:**
- Consumes: `histories: Mapping[str, pandas.DataFrame]`，至少包含 QQQ/SPY。
- Produces: `build_market_regime_frame(histories) -> pandas.DataFrame`。

- [ ] **Step 1: 写五状态、优先级、历史不足和前缀不变性的失败测试**

测试使用手工构造的 OHLCV 序列，断言 `regime`、`reason_codes`、
`regime_version`，并比较追加未来数据前后的历史输出。

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: `../../venv/bin/python -m unittest tests.test_market_regime -v`
Expected: FAIL with `ModuleNotFoundError: research.market_regime`。

- [ ] **Step 3: 实现最小状态机**

实现输入校验、因果滚动指标、固定优先级、证据字段和
`unavailable` 状态，不增加可调参数。

- [ ] **Step 4: 运行测试并确认通过**

Run: `../../venv/bin/python -m unittest tests.test_market_regime -v`
Expected: PASS。

- [ ] **Step 5: 提交状态机**

```bash
git add research/market_regime.py tests/test_market_regime.py
git commit -m "research: add causal market regime labels"
```

### Task 2: 阶段分层评估

**Files:**
- Modify: `research/run_expanded_walkforward_study.py`
- Modify: `tests/test_run_expanded_walkforward_study.py`

**Interfaces:**
- Consumes: 现有走步 `predictions` 与市场阶段 DataFrame。
- Produces: `evaluate_predictions_by_regime(predictions, regimes, horizons) -> pandas.DataFrame`。

- [ ] **Step 1: 写日期连接、指标分层和稀疏折排除的失败测试**

手工构造两个阶段、两个折次和 Ridge/Logistic 预测；期望结果包含
`regime`、`sample_count`、`comparable_fold_count` 和
`fold_win_rate_vs_ridge_current`。

- [ ] **Step 2: 运行测试并确认缺少接口而失败**

Run: `../../venv/bin/python -m unittest tests.test_run_expanded_walkforward_study -v`
Expected: FAIL because `evaluate_predictions_by_regime` is absent。

- [ ] **Step 3: 实现最小分层评估与 Markdown 渲染**

复用现有指标计算；非重叠样本沿用股票/折次抽样；只有同一折同一阶段
包含至少两个实际类别时才计算折次胜负。

- [ ] **Step 4: 运行相关测试并确认通过**

Run: `../../venv/bin/python -m unittest tests.test_market_regime tests.test_run_expanded_walkforward_study tests.test_market_direction_model -v`
Expected: PASS。

- [ ] **Step 5: 提交分层评估**

```bash
git add research/run_expanded_walkforward_study.py tests/test_run_expanded_walkforward_study.py
git commit -m "research: stratify walk-forward results by market regime"
```

### Task 3: 真实实验、报告与 TODO 证据

**Files:**
- Modify: `research/run_expanded_walkforward_study.py`
- Create: `reports/expanded-walkforward-regimes.csv`
- Create: `reports/expanded-walkforward-regimes.md`
- Modify after merge: `docs/modeling-todo.md`

**Interfaces:**
- Consumes: `data/research_prices.db` 和固定 240 股票研究队列。
- Produces: 阶段分层 CSV/Markdown 与更新后的 DATA-001 证据。

- [ ] **Step 1: 增加报告输出参数和清单字段的失败测试**

测试 CLI 辅助函数能渲染阶段覆盖、股票组结果、可比较折数和限制。

- [ ] **Step 2: 运行测试并确认新报告字段缺失**

Run: `../../venv/bin/python -m unittest tests.test_run_expanded_walkforward_study -v`
Expected: FAIL on missing regime report content。

- [ ] **Step 3: 实现报告并运行固定真实实验**

Run:
`../../venv/bin/python research/run_expanded_walkforward_study.py --database data/research_prices.db --start 2018-01-01 --max-tickers 240 --folds 5 --minimum-samples 1000`

- [ ] **Step 4: 检查报告完整性并写入结论**

确认五种状态覆盖、每组样本量、阶段内胜负和限制；不根据结果修改
`market_regime_v1`。

- [ ] **Step 5: 运行全量验证**

Run: `../../venv/bin/python -m unittest discover -s tests -v`
Expected: all tests PASS。

- [ ] **Step 6: 提交报告**

```bash
git add research/run_expanded_walkforward_study.py \
  tests/test_run_expanded_walkforward_study.py \
  reports/expanded-walkforward-regimes.csv \
  reports/expanded-walkforward-regimes.md
git commit -m "research: report walk-forward results by market regime"
```
