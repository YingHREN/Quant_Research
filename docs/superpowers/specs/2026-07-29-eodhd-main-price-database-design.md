# EODHD 主价格库设计

## 目标

让 `data/prices.db` 可以从已审计的 EODHD 研究价格库完整重建，
使主价格库不再依赖 Tiingo 的日频限流，同时保持现有模型与 UI
读取的 `prices(ticker, date, open, high, low, close, volume)` 接口不变。

## 范围

- 主库目标股票集合取自主库当前已有股票，并补齐模型参考 ETF。
- 每次切换必须为全量 EODHD 口径，不允许仅给 Tiingo 主库补写缺失股票。
- 复用 `research_prices.db.daily_prices` 已验证的
  `adjusted_open/high/low/close` 与原始 `volume`。
- 不修改预测模型、因子公式、图表协议或研究股票池。
- 保留现有 Tiingo 联网回填能力作为显式兼容入口，但默认 `--update`
  改为从 EODHD 研究库重建主库。

## 数据流

1. 从现有 `prices.db` 读取目标股票；若显式传入股票，则使用显式集合。
2. 合并 `REFERENCE_TICKERS`，得到排序且去重的目标集合。
3. 从 `research_prices.db` 读取每只股票当前历史分段中的 EODHD
   复权 OHLCV。
4. 对每只股票执行现有 `audit_history` 校验。
5. 写入与目标主库同目录的临时 SQLite 文件。
6. 为每只股票写入 `price_ingestions` 与 `price_coverage`，其中：
   - `provider = eodhd`
   - `adjustment = eodhd_adjusted_close_ratio_v1`
   - `requested_start` 为实际最早数据日期
7. 验证临时库：
   - `PRAGMA integrity_check = ok`
   - 目标股票全部存在
   - 每只股票至少一行
   - 每只股票最新日期等于研究库中该股票当前分段的最新日期
   - 不包含目标集合之外的股票
8. 使用 `Path.replace()` 原子替换 `prices.db`。

## 接口

在 `build_local_db.py` 增加：

```python
@dataclass(frozen=True)
class EODHDRebuildSummary:
    requested: int
    imported: int
    row_count: int
    first_date: str
    last_date: str
    integrity: str


def rebuild_from_eodhd(
    research_database,
    output_database,
    *,
    tickers=None,
    fetched_at=None,
) -> EODHDRebuildSummary:
    ...
```

CLI 行为：

- `--update`：默认从 `data/research_prices.db` 原子重建
  `data/prices.db`。
- `--provider tiingo`：保留原 Tiingo 一年重叠回填。
- `--provider eodhd`：显式选择 EODHD，和默认行为一致。
- `--research-database`：允许测试或离线任务传入其他研究库路径。

## 错误处理与回退

- 研究库不存在、缺少目标股票、存在无效 OHLCV、日期覆盖不一致或
  SQLite 完整性失败时，删除临时文件并保持原主库字节不变。
- 不自动退回 Tiingo，避免用户以为已经统一数据源。
- 若目标主库不存在且没有显式股票集合，直接失败，因为无法确定主库范围。
- 旧主库在原子替换前不重命名；实库操作前另行生成带时间戳的人工备份，
  备份不由日常更新命令无限累积。

## 测试

- EODHD 复权 OHLCV 被正确转换到主库列。
- 重建结果只包含目标股票，并记录 EODHD 来源和覆盖元数据。
- 缺失目标股票时失败且旧输出数据库哈希不变。
- 无效价格时失败且旧输出数据库哈希不变。
- CLI 默认使用 EODHD，显式 `--provider tiingo` 仍调用原回填路径。
- 在真实 196 只主库切换前后检查股票数、最新日期、完整性和代表股票价格。

