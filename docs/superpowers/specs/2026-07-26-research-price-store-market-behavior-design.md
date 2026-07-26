# 独立研究价格库与交易行为板块设计

## 目标

把 EODHD 扩充池归一化到独立、可追溯、可重复构建的研究价格库，并在 SEC 基本面板块之外增加基于 ETF 价格行为的 `market_behavior_v1` 分类。现有看板使用的 `data/prices.db` 不被覆盖。

## 数据边界

- 输入：
  - `data/cache/research_universe_liquid100m_v1.json`
  - `data/cache/eodhd_raw/<快照日期>/<ticker>.json`
  - 同目录下 `splits/` 与 `dividends/`
- 输出：`data/research_prices.db`
- 价格提供方：EODHD
- 每条记录保存提供方、导入时间、快照日期和复权方法。

## 价格归一化

日线同时保存：

- 原始 `open/high/low/close`
- EODHD `adjusted_close`
- `adjustment_factor = adjusted_close / raw_close`
- 用同日因子换算的 `adjusted_open/high/low`
- 原始成交量（EODHD 日线成交量已按拆股口径返回，不再乘分红调整因子）

拆股和分红进入独立事件表，原始字段不丢失。供应商在上市首日前返回的连续全零占位行会被忽略；一旦出现首条有效行情，后续全零行仍视为异常。其他无效价格、重复日期、负成交量、价格结构错误或非正复权因子会导致该证券事务回滚。

## 历史切段

相邻交易日间隔超过 180 个自然日时切换 `segment_id`。所有旧段保留，最后一段标记为当前段。模型默认可只训练当前段，避免 NBIS 等代码复用或长期停牌历史被错误拼接。

## 研究库表

- `security_master`：证券身份和 SEC 分类。
- `universe_memberships`：版本化股票池成员。
- `daily_prices`：原始与复权日线、历史段和数据血缘。
- `history_segments`：每个历史段的起止日期与样本数。
- `splits`、`dividends`：公司行动。
- `sector_classifications`：SEC 基本板块与市场行为板块并存。
- `import_runs`：构建结果、错误数和版本。

## `market_behavior_v1`

对股票、SPY 和 11 个板块 ETF 使用观察日之前最多 252 个共同交易日：

1. 计算日收益率。
2. 分别用 SPY 去除股票和板块 ETF 的大盘共同波动。
3. 比较股票残差与各板块 ETF 残差的相关性，最高者作为交易行为板块。
4. 同时记录残差 beta、63 日相对收益、共同样本数和置信度。
5. 与 SEC 板块分别展示；不一致时保存明确冲突原因，不覆盖 SEC 分类。

最少需要 126 个共同收益样本，所有计算严格截断在 `asof`，防止未来数据泄漏。

## 质量门槛

- SQLite `integrity_check = ok`。
- 导入可重复执行且不产生重复行。
- 每个入选股票至少 60 行；行为分类至少 126 个共同收益样本。
- 价格日期不得超过股票池观察日。
- 每个证券单独事务，失败不会污染其他证券。
- 自动测试覆盖复权、历史切段、幂等导入、点时截断、板块识别和冲突解释。
