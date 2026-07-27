# 退市证券历史身份与点时行业分类设计

## 目标

在已有 13,039 只退市普通股候选和 5,913,990 条有效日线之上，建立可审计的
证券身份、历史 SEC 行业观察和点时板块读取层。该层用于减少退市样本回测中的
代码复用、实体误连和“今天行业标签回填过去”问题。

本阶段首先完成身份与数据覆盖率实验；只有覆盖率、冲突率和来源成本经过真实
审计后，才允许全量采集历史申报或接入模型训练。

## 当前证据边界

- 活跃扩充池有 1,015 份 SEC submissions 身份缓存，研究库中 1,014 只证券
  拥有 `sec_sic_v1` 当前快照分类。
- 独立退市研究库有 13,039 只证券，其中 4,013 只保存无冲突有效 ISIN，
  8,655 只仅有供应商代码，371 只处于 ISIN 冲突状态。
- 退市研究库当前没有 CIK、历史 SIC 或带生效日期的行业分类。
- EODHD 退市 Fundamentals 可以提供最后已知公司快照，但不能证明该分类在
  更早观察日期已经成立。
- EODHD 历史指数成员与代码变更接口在当前套餐下返回 403；本设计不能假定
  它们可用，也不能用交易首末日期冒充成员区间。

## 核心原则

### 身份、基本面行业和交易行为分离

系统不得使用一个 `sector` 字段同时表达三种不同事实：

1. `security_identity`：某个供应商代码对应哪个法律申报实体；
2. `sec_industry`：SEC 在某个公开时点观察到的 SIC；
3. `market_behavior`：证券在某段历史中与哪个板块 ETF 的收益行为最接近。

三类记录必须分别保存来源、规则版本、观察日期和可用时间。模型和 UI 可以并列
展示，但不能用交易行为分类覆盖 SEC 分类，也不能把供应商快照标成历史 SEC
分类。

### 只使用当时可见的信息

所有历史读取统一使用：

```text
available_at <= forecast_asof
AND valid_from <= forecast_asof
AND (valid_to IS NULL OR forecast_asof < valid_to)
```

`filing_date`、报告期结束日和价格日期不是信息可用时间。SEC 信息默认在公开
申报的 `available_at` 后才可用于模型。

### 未知优于误连

- 当前 ticker 与当前 CIK 对照不能自动连接退市 ticker。
- 名称相似不能单独确认实体。
- 同一 ticker 在不同时期可能属于不同实体；同一实体也可能使用多个 ticker。
- 无可靠证据时保留 `unresolved`，不能为了提高覆盖率静默合并价格序列。
- 本阶段不修改或拼接 `daily_prices`。

## 数据来源与角色

### SEC submissions 与 EDGAR 申报

SEC submissions 是 CIK、当前和曾用名称、当前交易所/代码以及申报清单的主要
来源。优先使用 SEC 每晚发布的 submissions 全量 ZIP 做覆盖率筛选，再按需要
读取个别 CIK 的附加历史文件和公开申报头。

历史 SIC 只来自带 accession 和公开时间的 SEC 观察。若单份历史申报没有可
验证 SIC，则保持缺失，不能用 CIK 当前 SIC 回填该申报日期。

### EODHD

EODHD 只承担以下角色：

- 提供已保存的退市代码、名称、交易所和 ISIN；
- Fundamentals 可作为最后已知名称、ISIN、CIK、行业和退市状态的辅助证据；
- `General.UpdatedAt` 或采集快照日作为该供应商证据的观察时间。

EODHD 当前行业字段统一标为 `snapshot_only`。除非未来供应商明确提供带历史
生效日期的分类序列，否则不能生成历史行业区间。

### 价格与板块 ETF

已有复权日线用于生成独立的 `market_behavior_v2`。分类仅使用观察日期以前的
股票、SPY 和板块 ETF 收益，记录共同交易日、残差相关性、beta、相对收益和
覆盖窗口。它是市场行为代理，不是法律实体或基本面行业证据。

## 存储边界

新增独立、忽略版本控制的数据库：

```text
data/delisted_reference_data.db
```

它不修改以下数据库：

- `data/prices.db`
- `data/research_prices.db`
- `data/delisted_research_prices.db`

原始响应继续写不可变缓存；Git 只提交代码、测试、规格和不含供应商完整响应的
汇总报告。

## 数据模型

### `identity_evidence`

保存一条原始或规范化身份证据：

- `evidence_id`
- `candidate_ticker`
- `candidate_exchange`
- `key_type`：`isin`、`cik`、`ticker`、`former_name` 或 `legal_name`
- `key_value`
- `source`
- `source_record_id`
- `observed_at`
- `available_at`
- `confidence`
- `evidence_status`
- `raw_sha256`
- `reason_codes_json`

同一来源记录重复导入必须幂等。原始 token、请求 URL 查询参数和完整响应不得
进入数据库。

### `security_entity_links`

保存退市候选到 SEC 实体的裁决，不保存模糊概率：

- `candidate_ticker`
- `candidate_exchange`
- `cik`
- `link_status`：`confirmed`、`review_required`、`rejected`、`unresolved`
- `valid_from`
- `valid_to`
- `decision_rule`
- `rule_version`
- `decided_at`
- `supporting_evidence_json`
- `conflicting_evidence_json`

`confirmed` 只允许由强规则产生。不同 CIK 的强证据冲突必须进入
`review_required`；禁止自动选择多数来源。

### `sec_industry_observations`

保存 SEC 在公开申报中观察到的 SIC：

- `cik`
- `sic`
- `industry_label`
- `accession_number`
- `filing_type`
- `filing_date`
- `accepted_at`
- `available_at`
- `source`
- `raw_sha256`
- `parser_version`

主键必须阻止同一 accession 被重复写入。同一 accession 出现冲突 SIC 时整条
记录进入隔离表，不参与区间生成。

### `sec_industry_intervals`

由已排序观察确定性生成：

- `cik`
- `sic`
- `sector_key`
- `valid_from`
- `valid_to`
- `first_accession`
- `last_supporting_accession`
- `observation_count`
- `taxonomy_version`
- `interval_rule_version`

区间的 `valid_from` 为第一次可用观察时间，`valid_to` 为下一次不同 SIC 的
可用时间。相邻相同 SIC 观察合并并增加支持计数。第一次观察以前保持未知，
最后一次观察可以开放到 `NULL`，但读取结果必须显示最后支持日期和陈旧程度。

### `provider_classification_snapshots`

保存 EODHD 等供应商的最后已知分类：

- 候选证券键和可选 CIK；
- `sector`、`industry`、`snapshot_at`、`updated_at`；
- `source`、`raw_sha256`；
- 固定 `historical_eligibility = snapshot_only`。

该表不能被点时 SEC 分类读取器回退使用。

### `market_behavior_classifications`

保存按观察日生成的交易行为分类：

- 候选证券键；
- `asof`
- `sector_key`
- `benchmark_ticker`
- `common_days`
- `residual_correlation`
- `residual_beta`
- `relative_return`
- `confidence`
- `coverage_status`
- `rule_version`

每个 `asof` 只能使用该日以前的价格。共同样本不足时必须输出
`insufficient_history`，不能输出强制板块。

### 审计与隔离

数据库还需包含：

- `collection_runs`
- `source_artifacts`
- `identity_conflicts`
- `rejected_industry_observations`

所有批量导入都先写临时数据库，经完整性、外键、计数和内容哈希校验后原子替换。
空响应或短响应不得覆盖已有成功结果。

## 身份裁决规则

第一版规则优先级如下：

1. 同一证券的有效 ISIN 经 EODHD ID mapping 或 Fundamentals 返回唯一 CIK，
   且名称无明显冲突：可确认。
2. SEC 历史申报名、former name、历史 ticker 和交易日期窗口同时一致，并且
   没有其他 CIK 竞争：可确认。
3. 只有 ticker 相同，或只有名称模糊相似：保持 `review_required` 或
   `unresolved`。
4. 有效 ISIN 指向多个不相容实体、候选交易期与实体使用该代码的时期明显冲突，
   或证券类型冲突：`rejected` 或 `review_required`。
5. ADR、外国私人发行人和重组后的继承实体不得仅因名称接近而自动串联。

裁决规则必须返回稳定原因码和全部支持/反对证据，不能只输出一个置信分。

## 第一阶段：覆盖率实验

### 冻结样本

使用现有目录哈希冻结全部 13,039 只候选，并按以下层分层抽样：

- 无冲突强 ISIN；
- ticker-only；
- ISIN 冲突；
- 近期退市与早期退市；
- 美国本土名称与 ADR/外国发行人；
- 有历史日线与空历史响应。

抽样必须由候选稳定哈希决定，不能人工选择容易匹配的公司。

### 采集顺序

1. 下载并校验 SEC submissions 全量 ZIP；
2. 生成名称、former name、ticker 和 CIK 候选索引；
3. 对分层样本执行本地候选匹配；
4. 仅对仍不明确的样本请求有限量 EODHD Fundamentals/ID mapping；
5. 对已确认 CIK 抽取有限历史申报，验证 SIC 观察可获得性；
6. 输出全量成本和覆盖率外推，不直接启动 13,039 只全量 Fundamentals。

### 报告

生成：

```text
reports/delisted-identity-industry-coverage.md
reports/delisted-identity-industry-coverage.json
reports/delisted-identity-industry-coverage.csv
```

至少报告：

- 各身份层的确认、待复核、拒绝和未解析数量；
- SEC-only、SEC+ISIN、供应商辅助三种覆盖率；
- CIK 确认中的竞争候选数和冲突原因；
- 可获得历史 SIC 的 CIK 数、最早/最晚观察日和每十年覆盖；
- ADR、外国发行人、重组实体的缺口；
- 请求数、API 单位、下载字节、数据库预计大小和全量运行时；
- 不同匹配规则的有限审计样例；
- 目录与输入数据库哈希。

## 第二阶段：全量回填门槛

只有第一阶段同时满足以下条件，才进入全量身份和行业回填：

- 强规则确认样本的人工抽查误连率不高于 1%；
- 所有冲突都被隔离且不会写入 confirmed；
- 报告可以分别解释 SEC 历史覆盖和供应商快照覆盖；
- 点时读取测试证明未来 SIC 不会出现在更早观察日；
- 原始采集可续跑，失败或额度耗尽不会破坏已有缓存；
- 成本和存储估算处于当前资源预算内。

覆盖率不是硬性晋级指标。低覆盖但高精度可以继续用于分层研究；高覆盖但身份
误连不能晋级。

## 模型和 UI 接入边界

本设计阶段不直接改变 Ridge、向下风险专家或最终预测策略。全量回填和走步
验证完成后，模型才可以增加以下输入：

- 观察日 SEC 基本板块；
- 观察日交易行为板块；
- 两种分类是否一致；
- 分类覆盖率、陈旧程度和身份状态。

线上预测不得因缺失历史分类而回退到今天分类。UI 后续应并列显示“SEC 基本面
板块”“价格行为板块”和“身份/覆盖状态”，并明确标记供应商最后快照。

## 错误处理

- SEC ZIP、JSON、申报头或供应商响应结构异常时显式失败并保留错误摘要。
- CIK、ISIN、日期、accession 和 SIC 在写入前严格规范化。
- 非法时间区间、重复冲突、未来可用时间和未知规则版本禁止导入。
- 网络失败可重试；授权失败、配额不足和数据不存在必须分别记录。
- 不得在日志、报告、数据库或 Git 中记录 API token。

## 测试与验收

测试至少覆盖：

- ticker 复用不能自动合并不同 CIK；
- former name 与交易窗口共同支持时可确认；
- 单独名称相似不能确认；
- ISIN/CIK 冲突进入隔离；
- 相同 SIC 连续观察合并；
- SIC 变化形成半开区间；
- 第一次 SIC 观察以前读取为空；
- 未来申报不能影响过去预测；
- EODHD snapshot 不能作为 SEC 历史回退；
- 行为分类严格截断未来价格；
- 缓存复用、断点续跑、空响应保护和重复导入幂等；
- 三个现有价格数据库构建前后 SHA-256 不变；
- SQLite `integrity_check=ok` 且无外键错误；
- 全量测试、`git diff --check` 和密钥扫描通过。

完成第一阶段只关闭“身份与历史行业覆盖率实验”子项。只有全量点时分类导入、
覆盖审计和模型走步验证完成后，才能关闭长期 TODO 中的历史 SEC/行业分类项。
