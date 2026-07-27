# 退市普通股历史日线正式回填设计

## 目标与边界

对 `delisted_security_purification_v1` 接受的 13,039 个主交易所退市普通股
候选执行 2016-01-01 至 2026-07-27 的 EODHD 日线回填。下载必须可续跑、
可审计、失败不换代码，并在写入研究数据库前隔离空响应和非法行情行。

本阶段只保存原始 JSON、冻结清单、逐股质量审计和汇总报告，不写
`research_prices.db`，不把首末交易日解释为指数成员区间，也不自动拼接
冲突 ISIN 或新旧代码。

## 输入冻结

输入为完整净化目录：

```text
data/cache/eodhd_delisted_security_catalog/2026-07-27/catalog.json
```

运行器验证：

- `schema_version == delisted_security_catalog_v1`；
- `rule_version == delisted_security_purification_v1`；
- 文件 SHA-256 与提交的净化报告一致；
- 只选择 `backfill_eligible=true` 且 `classification=accepted_common` 的记录；
- `(exchange, ticker)` 唯一，候选总数固定为 13,039。

在第一个网络请求前，把按 `(exchange, ticker)` 排序的候选、目录哈希、查询窗口
和回填版本写入 `candidates.json`。后续续跑若目录哈希、候选、版本或窗口变化，
必须拒绝复用旧缓存；失败或空响应不能用别的证券替换。

回填版本固定为 `delisted_history_backfill_v1`。

## 下载与续跑

每只证券请求：

```text
GET /api/eod/<ticker>.US
from=2016-01-01
to=2026-07-27
period=d
order=a
fmt=json
```

- 默认并发 8；
- HTTP 429、500、502、503、504、网络超时和 JSON 解码错误最多重试 4 次，
  使用有限指数退避；
- 401、403、404 等永久错误不重试，并跨运行复用；
- 成功列表原子写入 `histories/<ticker>.json`，空列表也保存，因此不会反复请求；
- 已存在且可解析为列表的历史文件直接复用；损坏缓存重新下载；
- token 只从 `EODHD_API_TOKEN` 读取，不进入 URL 日志、manifest 或报告；
- 每完成 100 个 future 原子更新 `errors.json` 和 `manifest.json`，并打印
  processed/cached/downloaded/empty/errors 进度；
- 正常结束、异常结束和 `KeyboardInterrupt` 都写最终 checkpoint。可重试错误
  保持 `retryable=true`，下次运行重新请求。

`manifest.status` 只有在所有候选都拥有有效列表缓存或永久错误时才为
`complete`；仍有可重试错误时为 `incomplete`。永久错误不会被计为可用历史。

## 质量审计

复用 `audit_history_rows` 的因果无关 OHLCV 校验，为每只候选记录：

- 请求状态、原始/有效行数；
- 重复日期、非法日期/非有限值/非正价格/非法 OHLC 数量；
- 首末交易日、2018 年后有效行数；
- 原始字节数和缓存/下载来源；
- HTTP 状态、错误类型和是否可重试。

合法行计入覆盖统计，非法行只在未来暂存库导入时被隔离；原始响应保持不变。
汇总按交易所报告候选、可用、空、永久错误、可重试错误、质量警告、有效行数和
原始字节数。CSV 保存逐股审计，JSON/Markdown 保存汇总及最多 20 个错误样例，
避免把 13,039 条详情重复提交 Git。

## 产物

忽略的原始目录：

```text
data/cache/eodhd_delisted_history_backfill/2026-07-27/
  candidates.json
  histories/*.json
  errors.json
  manifest.json
```

提交的审计产物：

```text
reports/delisted-history-backfill.csv
reports/delisted-history-backfill.json
reports/delisted-history-backfill.md
```

实现分为：

- `research/delisted_history_backfill.py`：冻结候选、合并审计和汇总纯函数；
- `run_delisted_history_backfill.py`：网络、并发、原子缓存、checkpoint 和报告；
- 对应 unittest 覆盖冻结、断点续跑、永久/可重试错误、质量统计和 token 隔离。

## 验收

- 离线 fixture 证明候选在请求前冻结，失败不替换；
- 第二次运行不请求有效缓存和永久错误，只重试缺失/可重试项；
- checkpoint 中断后可续跑，目录或窗口变化明确拒绝；
- 真实回填结束后状态与 13,039 个候选逐项守恒；
- 报告无密钥，缓存未进入 Git，无 `.tmp` 文件；
- 全量测试和 `git diff --check` 通过；
- 完整原始数据验收后才进入独立暂存库导入阶段。
