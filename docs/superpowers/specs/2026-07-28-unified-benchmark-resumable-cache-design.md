# 统一向下风险基准可恢复缓存设计

## 目标

为 `research/run_unified_downside_benchmark.py` 增加独立、内容寻址、
可校验的研究缓存，使相同数据和相同研究定义的重复运行能够跳过统计模型
预测与风险规则上下文两个高成本阶段，同时保证冷运行与缓存运行产生完全
相同的标签、对齐结果、指标、晋级判断和报告。

缓存只服务离线研究，不进入 Flask 请求，不改变 Ridge、TOPRISK、
`forecast_decision_policy`、影子实验或线上模型权限。

## 非目标

- 不缓存或修改原始行情、分组、宏观数据和线上预测产物。
- 不把缓存命中解释为新的研究证据。
- 不允许因为缓存缺失、损坏或写入失败而改变模型输出。
- 不在首版实现跨机器共享、远程对象存储或分布式锁。
- 不缓存最终报告作为统计模型或规则上下文的替代品。

## 存储边界

使用独立 SQLite 文件 `data/unified_benchmark_cache.db`。不得写入：

- `data/research_prices.db`
- `data/prices.db`
- `data/analysis_cache.db`
- `data/downside_shadow.db`

SQLite 启用外键、有限 busy timeout 和 WAL。缓存数据库不进入 Git。

### `benchmark_cache_artifacts`

每行代表一个不可变阶段产物：

- `artifact_key`：完整身份的 SHA-256；
- `study_version`；
- `stage`：仅允许 `statistical_predictions` 或 `rule_predictions`；
- `created_at`；
- `database_fingerprint`；
- `assignment_fingerprint`；
- `config_fingerprint`；
- `code_fingerprint`；
- `schema_version`；
- `payload_codec`；
- `payload_size_bytes`；
- `row_count`；
- `payload_checksum`；
- `payload`。

`artifact_key` 为主键。相同身份与相同 payload 的重试幂等；相同身份出现
不同 payload 时拒绝覆盖并记录冲突，不静默更新。

## 缓存身份

缓存身份必须覆盖所有能改变阶段输出的输入。

### 数据库内容指纹

不能只使用文件路径、大小或修改时间。指纹按本次固定研究队列和参考资产
读取的实际内容计算，至少覆盖：

- ticker、交易日期；
- OHLCV 与复权相关字段；
- 历史覆盖边界；
- 点时分组的生效区间、主模型组、主题和来源；
- 市场状态输入所需参考 ETF。

同样行数的历史价格修订也必须改变指纹。

### 配置指纹

使用规范 JSON 计算 SHA-256，覆盖：

- `start_date`
- 固定研究股票队列及顺序
- `max_tickers`
- `folds`
- `horizons`
- `minimum_training_samples`
- 特征列表及顺序
- 中性区间、MAE 阈值和压力期适用范围
- 规则版本

`minimum_group_samples` 只影响评估，不影响两个被缓存阶段，因此不进入阶段
身份；它变化时复用预测并重新对齐评估。

### 代码指纹

不能只记录工作树当前分支名。首版使用：

- 版本化阶段 schema；
- `STUDY_VERSION`；
- 明确列出的模型、特征和规则版本；
- Git commit；
- 工作树脏状态。

工作树存在相关未提交修改时默认禁用缓存写入和读取，运行保持冷计算，并在
manifest 中显示 `disabled_dirty_worktree`。测试可以注入固定代码指纹。

## 负载格式与安全

环境没有 PyArrow，因此首版不引入新的大型依赖。使用规范化的列式 JSON
记录 DataFrame：

- 明确保存列名、列顺序、dtype、索引定义和记录；
- 日期统一为 ISO 8601；
- nullable Boolean、缺失值和 tuple 字段使用显式类型编码；
- JSON UTF-8 后使用 zlib 压缩；
- 压缩字节计算 SHA-256。

禁止使用 pickle，避免加载缓存时执行任意对象构造。读取顺序为：

1. 校验 artifact 身份；
2. 校验压缩负载 SHA-256；
3. 限制解压后最大字节数，拒绝压缩炸弹；
4. 校验 JSON schema、列、dtype 和主键；
5. 重建 DataFrame；
6. 执行阶段级语义验证。

任一步失败都视为 miss 并冷计算；损坏产物不会自动覆盖。只有显式
`--rebuild-cache` 才能为相同研究输入生成新的合法身份或替换经确认损坏的
本地缓存记录。

## 阶段边界

### 统计预测阶段

保存 `Mapping[specification, DataFrame]`，包括：

- Ridge 5/10/20 日；
- 通用 Logistic 5/10/20 日；
- 压力专用 Logistic 5/20 日。

读取时校验：

- 必须存在 `ridge_down`；
- 每个模型主键唯一；
- ticker、观察日、horizon、fold 合法；
- 模型版本、事件和连续分数类型合法；
- 不存在队列外 ticker 或配置外 horizon。

### 风险规则阶段

保存 `_build_rule_predictions` 的最终规则预测，而不是缓存不可复核的 Python
对象。包括：

- `immediate_8`
- `memory_12`
- `toprisk_confirmed`
- `toprisk_stateful`
- `ridge_plus_toprisk`

其身份除数据、配置和代码外，还引用统计预测 artifact key，因为组合规则
依赖 Ridge 测试键。统计预测变化会强制规则阶段失效。

## 运行流程

1. 读取固定输入并计算数据、分组、配置和代码指纹。
2. 查询统计预测 artifact。
3. 命中且验证通过则复用；否则冷计算。
4. 使用统计 artifact key 查询规则预测 artifact。
5. 命中且验证通过则复用；否则冷计算。
6. 标签、点时分层、评估和报告每次重新计算，避免评估配置变化复用旧结论。
7. 只有整个 benchmark 成功并原子发布报告后，才提交本次新缓存产物。

将缓存写入延迟到报告成功之后，避免失败运行留下看似有效但从未完整验收的
阶段结果。两个阶段在一个事务中提交；任一写入失败时都不影响报告结果，
manifest 标记 `cache_write_failed`。

## CLI

统一 runner 增加：

```text
--cache-database data/unified_benchmark_cache.db
--no-cache
--rebuild-cache
```

- 默认启用本地缓存；
- `--no-cache` 禁用读取与写入；
- `--rebuild-cache` 禁用读取并在成功运行后写入新产物；
- 两者同时出现时拒绝运行。

新增 `manage_unified_benchmark_cache.py`：

```text
status
verify
prune [--keep-per-stage N] [--apply]
```

`status` 和 `verify` 只读。`prune` 默认只预览；只有显式 `--apply` 才删除，
并报告删除身份、行数和可回收字节。

## Manifest 与进度

现有 `stage_timings_seconds` 保持不变，增加：

```json
{
  "cache": {
    "enabled": true,
    "database": "unified_benchmark_cache.db",
    "database_fingerprint": "...",
    "assignment_fingerprint": "...",
    "config_fingerprint": "...",
    "code_fingerprint": "...",
    "stages": {
      "statistical_predictions": {
        "status": "hit",
        "artifact_key": "...",
        "loaded_rows": 123
      },
      "rule_predictions": {
        "status": "miss_rebuilt",
        "artifact_key": "...",
        "loaded_rows": 456
      }
    },
    "write_status": "committed"
  }
}
```

阶段状态只允许：

- `hit`
- `miss_rebuilt`
- `miss_corrupt`
- `disabled`
- `disabled_dirty_worktree`

stderr 输出同样的稳定状态，但不得打印 payload、环境变量或凭证。

## 错误处理

- 缓存文件不存在：正常 miss。
- schema 版本不匹配：正常 miss，不迁移旧 payload。
- checksum、解压、JSON、dtype、主键或语义校验失败：`miss_corrupt`。
- SQLite 锁或读取失败：冷计算并显示安全错误码。
- 写入失败：保留成功报告，缓存状态为 `cache_write_failed`。
- 数据库内容、分组、配置、模型版本或代码变化：生成新身份，不覆盖旧身份。
- 任何缓存异常都不得缩小股票队列、改变标签或跳过评估。

## 测试与验收

### 单元测试

- 冷运行计算两个阶段并在完整成功后写入。
- 热运行不调用两个 builder，结果与冷运行逐列一致。
- 同行数价格修订导致 miss。
- 点时分组区间变化导致 miss。
- folds、horizons、特征顺序、规则版本或代码指纹变化导致 miss。
- `minimum_group_samples` 变化复用预测但重新评估。
- 损坏 checksum、截断 zlib、非法 JSON、错误 dtype 和重复主键均 fail closed。
- 统计 artifact 变化使规则 artifact 失效。
- 计算、评估或报告失败不写入任何新产物。
- 两阶段事务失败不留下部分缓存。
- `--no-cache` 和脏工作树不读写缓存。
- prune 默认不删除，`--apply` 只删除解析出的精确目标。

### 集成验收

- 在同一真实数据库上执行一次冷运行和一次热运行。
- 两次 JSON/CSV/Markdown 中除耗时、缓存状态和生成时间外，研究结果一致。
- 热运行的统计预测与规则上下文 builder 调用次数为零。
- `verify` 对所有 artifact 返回 checksum 和 schema 有效。
- 行情库、线上缓存、影子账本的 SHA-256 前后不变。
- 全量测试、compileall 和 `git diff --check` 通过。

## 发布与权限

该缓存属于研究执行优化：

- 不改变任何模型的 `online_authority`；
- 不改变统一 v2 或影子 v1 的晋级结论；
- 不进入网页模型面板；
- 缓存命中率和节省时间只作为工程观测，不作为预测质量证据。
