# 退市证券类型净化审计

- 规则版本：`delisted_security_purification_v1`
- 原始目录：32,371 行
- 主交易所范围内：14,912 行
- 可进入日线回填候选：13,039 行
- 输入 SHA-256：`ff34a7f7aa57907ac946f995c772e1b2f07f36527d83196728a6d641974898c7`
- 净化目录 SHA-256：`fa432000e8f77577175d2b13590dd0be65d44fef5ceceed433f3d33752ad80b0`
- 边界：该目录不是指数成员区间；无稳定身份键时不拼接价格序列。

## 分类结果

| 分类 | 数量 |
| --- | ---: |
| `accepted_common` | 13,039 |
| `rejected_non_common` | 1,549 |
| `needs_review` | 324 |
| `out_of_scope` | 17,459 |

## 身份覆盖

| 身份状态 | 全目录 | 主交易所范围内 |
| --- | ---: | ---: |
| `conflicting_isin` | 618 | 400 |
| `invalid_isin` | 5 | 5 |
| `strong_isin` | 7,350 | 4,248 |
| `ticker_only` | 24,398 | 10,259 |

## 原因审计

| 原因码 | 数量 | 样例代码 |
| --- | ---: | --- |
| `ambiguous_name` | 319 | AAIN, AAVL, ABGBY, ABILW, ACCUF |
| `debt_signal` | 17 | BMLP, CNFRL, CSSEN, CTDD, DUC |
| `identity_conflict` | 618 | AAC, AAC-UN, AACT, AACT-WT, AAI |
| `invalid_isin` | 5 | AGH, INB, LEXXW, METCL, MONDQ |
| `invalid_ticker` | 1,606 | AAAP_OLD, AACBU_OLD, AACB_OLD, AACI_OLD, AACT_OLD |
| `preferred_signal` | 15 | APE, BAMI, CLV, EBRB, EMMSP |
| `right_signal` | 86 | ACAXR, AITRR, ARIZR, ASCBR, ATAKR |
| `unit_signal` | 554 | AAC-U, AAC-UN, AACT-UN, AAM-UN, AAQC-UN |
| `unsupported_exchange` | 16,002 | AAAID, AAALF, AAALY, AAARF, AABNF |
| `warrant_signal` | 896 | AAC-WT, AACT-WT, AAM-WT, AAQC-WT, ACABW |
