# 行情更新后自动预热预测缓存设计

## 目标

行情任务写入新数据后，自动重建最近两个活跃观察日的持久化预测 artifact。用户不再
承担新数据后的第一次全市场计算；代价是后台行情更新任务延长约 1～2 分钟。

本功能延续 `PERF-002` 的 `data/analysis_cache.db`，不创建新的预测模型，不改变
Ridge、风险规则或个股 API 语义。

## 组件边界

新增 `ForecastCacheWarmer`，职责只有：

1. 从 repository 的 summaries 找到最近两个非 inactive 的 `latest_date`；
2. 按日期从旧到新加载完整 universe histories；
3. 依次调用同一个 `ForecastService.prewarm(histories)`；
4. 返回安全、可序列化的 cohort、行数、耗时和状态摘要。

从旧到新预热是必要约束：第二个更新的 cohort 会被现有 ForecastService 识别为
更完整快照并重建。最终内存 artifact 保持在最新 cohort，同时硬盘 store 保留两个
cohort，覆盖当前数据库中更新时间不同的活跃股票。

CLI `build_forecast_cache.py` 改为复用该组件，避免 CLI 和网页更新流程维护两套 cohort
选择规则。

## UpdateJobManager 生命周期

`UpdateJobManager` 保留现有 `on_success` 回调用于内存缓存失效。该回调失败仍是
正确性错误，因为继续使用旧内存 revision 可能返回陈旧预测。

新增独立的可选回调：

```python
on_cache_warmup: Callable[[], Mapping] | None
```

只要本次 run 至少成功写入一只股票，就按以下顺序执行：

1. 调用 `on_success()` 使 ForecastService revision 前进并清空内存 bundle；
2. 将 warmup 状态设为 `running`；
3. 调用 `on_cache_warmup()`；
4. 成功则状态设为 `ready`；
5. 失败则记录安全错误码 `cache_warmup_error`，状态设为 `failed`；
6. 发布原行情任务的 terminal state。

预热失败不能把已成功写入的行情任务从 `completed`、`partial` 或 `rate_limited`
改成 `failed`，也不能把 remaining ticker 状态改成不可恢复。下一次个股查询仍会
使用 `PERF-002` 的自动冷构建兜底。

如果本轮没有写入任何价格，不调用失效或预热，warmup 状态为 `skipped`。

## 状态合约

`JobSnapshot.to_dict()` 增加：

| 字段 | 值 |
|---|---|
| `cache_warmup_state` | `idle`、`running`、`ready`、`failed`、`skipped` |
| `cache_warmup_error` | `null` 或 `cache_warmup_error` |
| `cache_warmup_started_at` | ISO UTC 时间或 `null` |
| `cache_warmup_finished_at` | ISO UTC 时间或 `null` |
| `cache_warmup_cohorts` | 成功摘要中的观察日列表；失败时为空 |

更新 worker 在预热期间仍保持 `state=running`，前端轮询会继续等待。完成后原有
terminal state 不变。

## Flask 装配

仅当 `create_app()` 自己创建默认 `UpdateJobManager` 时，传入：

- `on_success=forecast_service.invalidate`；
- `on_cache_warmup=ForecastCacheWarmer(repository, forecast_service)`。

显式注入的 `UPDATE_MANAGER`、`FORECAST_SERVICE` 或测试 fake 不被改写。若注入的
forecast service 没有 `prewarm()`，自动预热关闭，更新流程保持旧行为。

## 并发与查询

预热在现有单 worker 更新线程中同步执行，因此同一 manager 不会出现两个预热任务。
`ForecastService` 自身的 `RLock` 保证预热与网页查询不会同时发布冲突 artifact。

如果用户在行情更新尚未完成时查询，查询可能等待 ForecastService 锁；前端已经把
行情任务显示为 running。第一版不再启动第二个预热线程，避免重复重建、磁盘竞争和
状态竞态。

## 错误与安全

- repository 读取失败、artifact 写入失败或 prewarm 异常只影响 warmup 状态；
- 日志可以记录 traceback，但 API 只返回固定错误码；
- 不向网页返回本地数据库路径、SQLite 错误或 pickle 细节；
- 预热不写 `prices.db`；
- 预热失败不删除旧 artifact；内容签名确保旧 artifact 不会误命中新行情。

## 测试与验收

必须验证：

- 有写入的 completed、partial、rate-limited run 均先 invalidate 再 warmup；
- 零写入 run 不失效、不预热；
- warmup 失败保留原 terminal state 和 resumable 语义；
- invalidation 失败仍使用既有 `cache_invalidation_error` 失败语义且不开始预热；
- snapshot 在 running、ready、failed、skipped 状态下 JSON 合约稳定；
- warmer 选择最近两个 active cohort，排除 inactive，并按旧到新调用；
- CLI 与网页更新复用同一 warmer；
- 并发 start 仍被拒绝，预热期间 manager 始终处于 running；
- 现有 508 项测试无回归。

真实验收：

1. 对一个测试价格库执行至少一条成功更新；
2. 确认 terminal snapshot 的 warmup 为 `ready`；
3. 确认 `analysis_cache.db` 存在新行情签名对应的两个 cohort；
4. 启动全新 app，INTC 或另一个活跃 cohort 股票首次请求目标不超过 5 秒。

