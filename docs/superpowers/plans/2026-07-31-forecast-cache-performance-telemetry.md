# 预测缓存性能遥测实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `PERF-002` 补齐可比较的缓存命中率、读取耗时、重建耗时、payload 大小和失败次数，同时保持缓存 best-effort 降级与模型输出不变。

**Architecture:** `ForecastArtifactStore` 只在进程内记录 load/save 结果和耗时，不迁移 SQLite schema；`ForecastService` 记录 revision-wide artifact 的内存命中、磁盘命中和重建结果，再通过现有 `/api/cache/status` 安全返回聚合值。所有计数随进程重启归零，payload 大小继续读取现有 SQLite `LENGTH(payload)` 汇总。

**Tech Stack:** Python 3.9、`time.perf_counter()`、SQLite、`unittest`

## Global Constraints

- 不修改 Ridge、特征、风险上下文、缓存 key、payload 编码或决策权限。
- 不把遥测写入 `prices.db`、`analysis_cache.db` 或任何模型产物。
- 读取、解码、写入失败继续表现为安全 miss，不暴露路径或异常文本。
- `hit_rate` 统计 revision-wide artifact 访问：`(memory_hit_count + disk_hit_count) / access_count`；无访问时为 `None`。
- `failure_count` 合并存储读写失败、磁盘 artifact 恢复失败和 artifact 重建失败；正常 miss 不计为失败。store 提供合法计数时视为存储层权威，未提供时使用 service 边界计数，保证同一异常不双计。
- 所有耗时为非负秒数；尚未发生对应操作时为 `None`。

---

### Task 1: 增加进程内缓存性能遥测

**Files:**
- Modify: `web/services/forecast_artifacts.py`
- Modify: `web/services/forecasts.py`
- Modify: `web/app.py`
- Modify: `tests/test_web_forecast_artifacts.py`
- Create: `tests/test_web_forecast_cache_telemetry.py`
- Modify: `docs/modeling-todo.md`

**Interfaces:**
- Produces: `ForecastArtifactStore.status()` 新增 `load_count`、`load_hit_count`、`load_miss_count`、`save_count`、`save_success_count`、`failure_count`、`load_hit_rate`、`last_read_seconds`、`last_write_seconds`。
- Produces: `ForecastService.cache_status()` 新增 `access_count`、`memory_hit_count`、`disk_hit_count`、`rebuild_count`、`rebuild_failure_count`、`hit_rate`、`failure_count`、`last_read_seconds`、`last_rebuild_seconds`。

- [x] **Step 1: 写 store 失败测试**

在 `tests/test_web_forecast_artifacts.py` 新增真实 round-trip 测试：一次 save、一次命中 load、一次正常 miss 后断言 `load_count == 2`、`load_hit_count == 1`、`load_miss_count == 1`、`save_count == save_success_count == 1`、`load_hit_rate == 0.5`、失败为 0，两个耗时均为非负数。再破坏 checksum，断言一次读取失败会增加 `failure_count`，但不增加正常 miss。

- [x] **Step 2: 运行 store 测试确认 RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest \
  tests.test_web_forecast_artifacts.ForecastArtifactStoreTest.test_status_tracks_hit_miss_failure_and_io_timings -v
```

Expected: FAIL，因为 `status()` 尚无 `load_count` 等字段。

- [x] **Step 3: 写 service 失败测试**

在 `tests/test_web_forecast_cache_telemetry.py` 使用两个 80 日真实 OHLCV fixture 和临时 `ForecastArtifactStore`：首个 service 冷 prewarm 后断言一次重建、命中率 0；同 service 再 prewarm 后断言一次内存命中、命中率 0.5；新 service prewarm 后断言一次磁盘命中、命中率 1.0。所有状态保留 `research` 之外的既有模型行为，不断言私有计时常量。

- [x] **Step 4: 运行 service 测试确认 RED**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest \
  tests.test_web_forecast_cache_telemetry -v
```

Expected: FAIL，因为 `cache_status()` 尚无 `access_count`、`hit_rate` 和 `last_rebuild_seconds`。

- [x] **Step 5: 实现最小 store 遥测**

用 `perf_counter()` 包围真实 load/save 操作；在现有 `RLock` 内更新计数。`row is None` 记正常 miss；SQLite、解码、checksum、codec 或写入错误记 failure；状态查询只复制计数，不修改数据库 schema。同步扩展 `web.app._unavailable_cache_status()`，保证服务缺失或异常时仍返回同形 typed telemetry。

- [x] **Step 6: 实现最小 service 遥测**

每次 `_revision_artifacts()` 只记一个访问结果：`memory_hit`、`disk_hit` 或 `rebuilt`。磁盘 artifact 无法重建合法 provider 时记失败并安全回退冷重建；重建用 `perf_counter()` 计时，异常增加 `rebuild_failure_count` 后继续原样抛出。`cache_status()` 从 store 状态读取 payload 大小和最后读取耗时；合法 store 失败计数为存储层权威，无 status 时使用 service 边界计数，缺少可选 status 或返回畸形计数时保留安全默认值。

- [x] **Step 7: 运行聚焦测试确认 GREEN**

Run:

```bash
PYTHONPYCACHEPREFIX=/private/tmp/stock-screener-pycache ./venv/bin/python -m unittest \
  tests.test_web_forecast_artifacts \
  tests.test_web_forecast_cache_telemetry \
  tests.test_web_api.ForecastServiceTest.test_cache_status_tracks_rebuild_memory_hit_and_disk_hit \
  tests.test_web_api.ForecastServiceTest.test_cache_status_is_safe_when_store_status_fails -v
```

Expected: PASS。

- [x] **Step 8: 更新中文 TODO 并做回归**

把 `PERF-002` 的性能基线项标记为已完成，记录字段语义和进程内归零限制；运行缓存/API 相关测试、完整 `unittest discover`、服务健康检查，并确认只保留既有 WAL/SHM 与用户研究脚本。

- [x] **Step 9: 提交 main**

```bash
git add \
  docs/modeling-todo.md \
  docs/superpowers/plans/2026-07-31-forecast-cache-performance-telemetry.md \
  tests/test_web_forecast_artifacts.py \
  tests/test_web_forecast_cache_telemetry.py \
  web/app.py \
  web/services/forecast_artifacts.py \
  web/services/forecasts.py
git commit -m "perf: expose forecast cache telemetry"
```
