# 退市普通股历史日线分层试验设计

## 目标

使用当前 EODHD EOD Historical 套餐可访问的数据，固定抽取 250 只美国主交易所
退市普通股，测量历史日线可用率、2018 年后相关性、质量问题、下载体积和全量
回填规模。试验只产生研究证据和不可变缓存，不写生产价格库，不把首末交易日
解释成指数成员区间。

## 冻结样本

输入固定为 EODHD：

```text
/api/exchange-symbol-list/US?delisted=1&type=common_stock
```

仅保留：

- `Exchange` 为 `NASDAQ`、`NYSE` 或 `NYSE MKT`；
- `Currency` 为 `USD`；
- `Type` 大小写归一后为 `common stock`；
- 代码符合 `^[A-Z][A-Z0-9.-]{0,14}$`，排除带下划线的供应商旧代码别名。

每个交易所内部按
`sha256("delisted_history_pilot_v1|<exchange>|<ticker>")` 排序，固定选择：

- NASDAQ：100；
- NYSE：100；
- NYSE MKT：50。

样本清单在任何日线请求前写入 manifest。空响应、HTTP 错误或质量失败不会替换
为另一只股票，防止结果导向的重抽样。

## 日线采集

每只代码请求：

```text
/api/eod/<ticker>.US
from=2016-01-01
to=2026-07-27
period=d
order=a
fmt=json
```

并发固定为 8，HTTP 429 和 5xx 使用有限指数退避；401/403/404 不重试。每只
股票的成功 JSON 或结构化错误都原子落盘。已存在且结构有效的文件复用，支持
限流后继续运行。token 只来自 `EODHD_API_TOKEN`，不得写入文件或日志。

## 质量审计

每只股票保留以下指标：

- 请求状态：成功、空响应、HTTP 错误、结构错误；
- 原始行数、去重交易日数、重复日期数；
- 非法日期、非有限数值、非正价格和非法 OHLC 行数；
- 第一/最后交易日、2018-01-01 后行数；
- 是否在 2018 年后仍有交易；
- 原始 JSON 字节数；
- 名称或代码是否带 `warrant`、`unit`、`preferred`、`-WT`、`-WS`、
  `-U`、`-UN` 等可疑普通股标签。

本试验不因单行异常删除整个响应。只有日期与 OHLCV 全部合法的行计入有效行；
异常计数独立报告，方便后续决定生产导入的拒绝阈值。

## 规模估计

按交易所分别报告：

- 目录候选数、固定样本数；
- 成功/空/错误率；
- 2018 年后仍交易的比例；
- 平均、中位数和 P90 行数/字节数；
- 以候选数乘样本成功率与平均/P90 体积得到的全量 EOD 请求、行数和未压缩
  JSON 规模估计。

总估计只覆盖 NASDAQ、NYSE、NYSE MKT，不外推 PINK 或其他 OTC 市场。
估计必须同时给出点估计和 P90 上界，禁止只报告较小数字。

## 产物与边界

新增：

- `research/delisted_history_pilot.py`：纯抽样与质量审计；
- `run_delisted_history_pilot.py`：可续跑采集与报告；
- `reports/delisted-history-pilot.{md,json,csv}`：真实试验结果。

原始目录和单股 JSON 位于忽略的 `data/cache/`，不提交 Git。报告不包含 token
或完整原始响应。

该试验不能关闭历史股票池 TODO，因为首末交易日只证明证券存在；它可以决定
下一步是否值得回填主交易所退市日线，以及需要多少请求、时间和磁盘。
