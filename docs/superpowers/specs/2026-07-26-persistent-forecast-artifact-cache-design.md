# 持久化预测产物缓存设计

## 目标

解决服务进程重启后首次访问个股需要约 59 秒的问题。当前耗时主要来自
`ForecastService` 重建全市场特征表和风险上下文，而不是个股 K 线本身。

实现后，服务应把已经计算好的全市场预测产物保存到独立硬盘数据库。只要行情内容、
模型版本和特征版本没有变化，新的服务进程就直接读取缓存，不再重复计算。

## 范围

本阶段缓存以下 revision-wide 产物：

- 已附加未来标签的全市场特征表；
- 全市场点时风险上下文；
- 预测评估结果；
- 行情覆盖范围与内容指纹；
- 模型、特征、风险上下文和缓存格式版本。

`RidgeForecastProvider` 不直接序列化。缓存命中后使用已验证的特征表重新构造
provider，避免持久化运行时对象。最终单股 JSON bundle 继续使用现有内存 LRU；
单股图表、VCP、支撑压力等仍在访问该股票时按需计算。

## 存储位置与表结构

缓存写入：

```text
data/analysis_cache.db
```

它与原始行情库 `data/prices.db` 完全分离。缓存库可以删除并自动重建，不能成为
任何模型或行情数据的唯一来源。

SQLite 表 `forecast_artifacts` 包含：

| 字段 | 含义 |
|---|---|
| `cache_key` | 行情签名与全部算法版本组合后的稳定主键 |
| `market_signature` | 所有股票覆盖范围和内容指纹的组合摘要 |
| `model_key` / `model_version` | Ridge 模型身份 |
| `feature_version` | 特征及标签构建版本 |
| `risk_context_version` | 风险上下文构建版本 |
| `format_version` | 持久化协议版本 |
| `created_at` | UTC 创建时间 |
| `payload_codec` | 序列化与压缩协议 |
| `payload_checksum` | 压缩 payload 的 BLAKE2 校验值 |
| `payload` | 特征表、风险上下文、评估和快照元数据 |

默认最多保留最近两个有效 artifact，避免缓存无限增长。

## 命中与失效

每次 `ForecastService.build()` 已经会计算所有历史的 coverage 和内容 fingerprint。
服务将这些值合成为 `market_signature`，再与以下版本共同生成 `cache_key`：

- 模型 key/version；
- 特征版本；
- 风险上下文版本；
- 持久化格式版本。

只有所有字段完全一致才算命中。因此：

- 服务重启但行情未变：命中；
- 行情新增、修订或股票池变化：未命中并重建；
- Ridge、特征、标签或风险规则升级：未命中并重建；
- SQLite 行损坏、checksum 不符或反序列化失败：删除坏行并重建；
- 测试注入的 provider/evaluator：默认不使用持久缓存，避免污染生产缓存。

内存中的 `database_revision` 继续处理同一进程内更新竞争；硬盘缓存不依赖进程重启后
会归零的 revision 数字，而依赖真实行情内容签名。

## 写入和并发安全

artifact 只有在特征表、provider、风险上下文和评估全部成功构建后才写入。
SQLite 写入使用单事务 UPSERT，payload 同时保存 checksum。读取和写入由
`ForecastArtifactStore` 内部锁保护；多个请求在同一进程仍由 `ForecastService`
现有锁合并为一次冷构建。

payload 使用本地 Python pickle 协议和 zlib 压缩。该数据库只允许应用自身生成和
读取；缓存路径不得接受 HTTP 参数。反序列化后仍必须重新验证 DataFrame 索引、
必要列、provider 身份和版本，不能因命中缓存而跳过模型输入校验。

## 数据更新与预热

第一步实现持久化命中：首次生成缓存仍需一次完整计算，后续服务重启直接复用。

同时提供显式 `ForecastService.prewarm(histories)` 接口，供数据更新流程或维护脚本
调用。更新成功后可以在更新任务内重建缓存，使等待发生在“更新数据”阶段，而不是
用户第一次搜索股票时。预热失败不得破坏旧行情数据或令更新任务回滚；应记录安全
错误状态，下一次个股访问仍可自动重建。

## 配置

- `FORECAST_ARTIFACT_CACHE_PATH`：默认 `data/analysis_cache.db`；
- `FORECAST_ARTIFACT_CACHE_ENABLED`：生产默认开启，测试默认按是否显式传入路径决定；
- `FORECAST_ARTIFACT_CACHE_ENTRIES`：默认 2；
- 自定义 provider/evaluator 默认禁用持久化，除非显式提供稳定的持久化身份。

## 错误处理

- 缓存不存在：正常冷构建；
- 数据库只读、锁定或写入失败：当前请求继续使用刚计算出的内存产物；
- 缓存读取损坏：忽略并尝试删除坏行，再冷构建；
- 磁盘缓存错误不会向网页暴露本地路径、SQLite 信息或 pickle 错误；
- 原始 `prices.db` 永远不因缓存失败而修改。

## 测试与验收

必须覆盖：

- 第一个 service 构建并落盘，第二个全新 service 命中且不调用
  `build_feature_frame` / `build_forecast_risk_context`；
- 行情新增、同形状历史修订、模型版本、特征版本和风险版本变化均未命中；
- checksum 损坏、截断 payload、未知格式均安全重建；
- 写入失败不影响当前预测；
- 缓存条目数量有界；
- 测试注入 provider 默认不落盘；
- 并发冷请求只生成一份有效 artifact；
- 全量 API、更新任务和预测测试无回归。

使用本地完整价格库验收：

- 先生成一次 `analysis_cache.db`；
- 启动全新 Flask app；
- 测量 INTC 冷进程首次请求；
- 目标从当前约 58.7 秒降低到 5 秒以内；
- 同进程热请求继续保持约 1 秒；
- 如果未达到目标，报告真实计时和剩余函数热点，不修改目标记录。
