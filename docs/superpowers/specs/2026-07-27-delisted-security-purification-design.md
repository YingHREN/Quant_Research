# 退市证券类型净化与身份键设计

## 目标与范围

在正式下载数千只退市证券历史前，对 EODHD 美国退市目录建立确定性、可审计的
证券类型净化层。该层只决定“是否可以进入普通股日线回填候选”，不推断指数成员
区间，不自动拼接新旧代码，也不写生产价格库。

本阶段只完成目录净化、身份提示和真实全目录审计。正式日线下载、SEC 历史行业
回填与价格库导入分别留给后续阶段。

## 输入与范围门

输入是 EODHD `exchange-symbol-list/US?delisted=1&type=common_stock` 的原始
JSON 数组。每行必须保留 `Code`、`Name`、`Exchange`、`Currency`、`Type` 和
`Isin` 的原始值用于审计。

范围门只让以下证券进入三态类型判断：

- 交易所为 `NASDAQ`、`NYSE` 或 `NYSE MKT`；
- 币种为 `USD`；
- 供应商类型归一后为 `common stock`；
- 代码符合 `^[A-Z][A-Z0-9.-]{0,14}$`。

不满足范围门的行标记为 `out_of_scope`，不能进入正式回填候选，但仍计入汇总。

## 三态类型判断

规则版本固定为 `delisted_security_purification_v1`。范围内证券按优先级判断：

1. `rejected_non_common`
   - 名称明确含 `warrant`，或代码带 `-WS`、`-WT`、`-W`；
   - 名称以 `unit/units` 结尾、明确写 `corporate/preferred unit`，或代码带
     `-U`、`-UN`；
   - 名称以 `right/rights` 结尾或明确写 `right to`，或代码带 `-R`、`-RT`；
   - 名称明确写 `preferred stock/share/unit/series`、`participating preferred`
     或代表优先股的 depositary share；
   - 名称以利率、到期、senior/subordinated、fund/trust 等上下文明确表示
     note、bond 或 debenture；
   - 名称含 `ETF`、`exchange traded fund`、`closed-end fund`。
2. `needs_review`
   - 名称为空、只等于代码、含测试证券标记；
   - 有 ISIN 字段但格式或校验位非法；
   - 同一交易所和代码出现冲突记录。
3. `accepted_common`
   - 通过范围门且没有上述反证或歧义。

SPAC 名称中的 `Acquisition Corp` 本身不是拒绝理由；只有其 unit、warrant 或
right 证券被拒绝。普通公司名称以字母 `W`、`U` 或 `R` 结尾也不是拒绝理由，
除非名称或带分隔符的后缀提供明确证据，避免误杀 `MWW` 一类普通股代码。
`Preferred Apartment Communities`、`Unit Corporation`、`American Bank Note`
等公司名称和不代表优先股的普通 ADS 也不是证券类型反证。

每行输出稳定原因码、命中的名称/代码证据、规则版本和 `backfill_eligible`。
只有 `accepted_common` 的该字段为真。

## 身份键与禁止事项

身份契约与类型判断分离：

- 通过 ISO 6166 长度、字符集和 Luhn 校验的 ISIN 生成
  `identity_key = "isin:<ISIN>"`，状态为 `strong_isin`；
- 缺失 ISIN 时不生成稳定身份键，状态为 `ticker_only`；
- 非空但非法的 ISIN 状态为 `invalid_isin`，证券进入 `needs_review`；
- 未来取得带来源时间的 CIK 时只作为独立身份提示保存；单凭今天的
  ticker→CIK 对照不得连接退市代码，避免代码复用导致错连。

不同目录行若出现同一有效 ISIN 但公司名称或证券类型决策冲突，保留各自独立
类型判断和回填资格，但身份状态改为 `conflicting_isin`、清空稳定身份键并记录
`identity_conflict`。身份冲突不能把范围外证券改成范围内，也不能把明确权证改
成普通股。没有稳定身份键的代码之间不得自动合并价格序列。

## 产物与数据流

- `data/delisted_security_catalog.py`：纯净化、ISIN 校验、冲突处理和汇总；
- `build_delisted_security_catalog.py`：读取原始目录，写不可变净化目录与
  Markdown/JSON 审计；
- `data/cache/eodhd_delisted_security_catalog/2026-07-27/catalog.json`：
  忽略的完整净化目录；
- `reports/delisted-security-purification.{md,json}`：提交的汇总证据，不含
  token 或完整供应商响应。

报告记录输入 SHA-256、规则版本、各交易所的范围内数量、三态数量、拒绝原因、
ISIN 覆盖率、冲突数和每种原因的有限样例。输出采用原子替换；相同输入和规则
必须产生字节稳定的净化目录与统计内容，运行时间戳只进入单独 manifest。

## 错误处理与验收

- 非数组输入、非映射行和范围内重复 `(exchange, ticker)` 必须明确失败；
- 原始字段不能因归一化而丢失；
- 原因码未知或分类状态与 `backfill_eligible` 不一致必须失败；
- 真实 32,371 行目录完成审计，报告能解释试点中 24 个明显误分类；
- 全量测试、`git diff --check`、密钥扫描通过，完整目录缓存不得进入 Git。

该阶段关闭“证券类型二次净化与身份契约”子项，但不会关闭正式退市日线回填或
历史点时成员子项。
