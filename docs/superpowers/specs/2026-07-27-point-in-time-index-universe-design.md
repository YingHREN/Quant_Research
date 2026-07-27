# 历史点时指数股票池设计

## 目标

建立第一版可审计、无“今天成分回填过去”的历史股票池，为向下路径风险专家
提供真实观察日期上的 S&P 500 成员。该阶段优先解决当前股票池的幸存者偏差，
不声称已经补齐历史行业分类，也不一次性下载全美数万只退市股票。

## 数据边界

数据源固定为 EODHD `GSPC.INDX` 的
`HistoricalTickerComponents`。每条记录必须包含：

- `Code`：当时使用的证券代码；
- `StartDate`：加入指数的生效日期；
- `EndDate`：移出指数的生效日期，现任成员为 `null`；
- `IsActiveNow` 与 `IsDelisted`；
- 可选 `Name`。

成员区间使用半开语义 `[StartDate, EndDate)`：加入日开始有效，移出日不再
属于指数。任何缺失或非法 `Code`/`StartDate`、`EndDate <= StartDate`、
重复且内容冲突的记录都必须拒绝整个导入事务。历史记录可以晚于其生效日期
被研究系统采集，因为它描述的是公开发生过的成员事实；原始快照日期和导入
时间仍必须保留，以便审计数据来源，而不能把采集时间误作成员生效时间。

官方接口与字段定义：

- `https://eodhd.com/api/fundamentals/GSPC.INDX`
- `filter=HistoricalTickerComponents`
- 官方文档：
  `https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds`

## 存储

复用已有 `universe_memberships` 表的有效区间，不新建平行成员真相表。为现有
表增加以下可空列，兼容当前数据库：

- `source`；
- `source_snapshot_date`；
- `imported_at`；
- `is_delisted`；
- `security_name`。

历史指数股票池的 `universe_key` 固定为 `sp500_historical_eodhd_v1`，
`selection_rule` 同值。导入前先以提供商代码建立最小 `security_master`
记录；这只表示目录身份存在，不代表价格已完成回填。导入采用事务内的
“同一 universe_key 整体替换”，避免短响应与旧记录混合。空响应禁止替换。

代码变更历史使用独立 `security_symbol_changes` 表保存：

- `old_symbol`、`new_symbol`、`effective_date`；
- `exchange`、`company_name`；
- `source_snapshot_date`、`imported_at` 和 `source`。

该表本轮完成标准化与幂等存储，但不自动拼接价格序列。没有可靠 CIK/ISIN
身份匹配时，旧代码与新代码必须继续作为两个价格实体，避免错误合并。

## 读取契约

`ExpandedMarketDataRepository.load_universe_members(universe_key, asof)`
只返回满足以下条件的成员：

```text
effective_from <= asof
AND (effective_to IS NULL OR asof < effective_to)
```

结果携带区间、退市状态和来源审计字段。必须显式传入 `asof`，禁止默认使用
最新日期。读取不存在的股票池返回空映射，不回退到当前 `security_master`
或当前 SEC 分类。

同时提供批量观察日期接口，把有限个观察日期映射为各自有效代码集合；SQL
读取一次全部相关区间，再在内存按半开区间展开，避免研究循环逐日查询。

## 原始采集

新增独立命令：

```text
collect_eodhd_point_in_time_universe.py
```

默认采集：

1. `GSPC.INDX` 历史成分；
2. 指定日期范围内的美国代码变更。

命令只写不可变原始 JSON 和清单，不直接修改研究数据库。若目标快照已存在且
JSON 结构有效则复用；下载先写临时文件再原子替换。API token 只从
`EODHD_API_TOKEN` 环境变量读取，禁止进入命令行日志、报告或数据库。

独立导入命令读取原始快照并写入研究数据库。采集和导入分开，使供应商权限、
限流或短响应不会破坏现有数据库。

## 验证与暂不完成事项

固定样例测试必须覆盖：

- 字典和嵌套响应两种官方 JSON 形态；
- 半开区间边界；
- 退市成员仍可在历史日期出现；
- 后来加入的成员不能出现在更早日期；
- 空响应、非法日期、冲突重复的事务回滚；
- 重复导入幂等；
- 代码变更只记录映射、不擅自合并证券。

真实回填后输出成员数量、退市成员数量、最早/最晚生效日期、2018 至今每年
年末覆盖、当前研究价格库覆盖与缺失代码清单。

本阶段不会关闭“历史股票池成员和板块映射”总 TODO。只有历史成员部分可标为
完成；历史 SEC/行业分类与退市证券日线仍保留为后续任务。向下风险专家也不会
因此直接获得线上否决权。
