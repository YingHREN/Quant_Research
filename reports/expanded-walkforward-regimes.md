# 市场阶段分层走步诊断

- 数据截止：2026-07-24
- 研究股票：240 只
- 样本起点：2018-01-01
- 走步折数：5
- 状态规则：`market_regime_v1`，只使用观察日及此前 QQQ/SPY。
- 执行口径：观察日收盘后生成信号，下一交易日开盘进入。
- 结果只用于诊断，不修改 Ridge、Logistic 或线上决策。

## 阶段覆盖

| regime | 阶段 | session_count | start_date | end_date | session_share |
| --- | --- | --- | --- | --- | --- |
| acute_selloff | 急跌 | 63.0000 | 2018-02-05 | 2025-04-08 | 0.0293 |
| correction | 修正 | 346.0000 | 2018-02-09 | 2026-07-24 | 0.1609 |
| range_bound | 震荡 | 195.0000 | 2018-04-26 | 2026-07-14 | 0.0907 |
| under_pressure | 市场承压 | 476.0000 | 2018-02-02 | 2026-07-23 | 0.2213 |
| uptrend | 上涨趋势 | 1071.0000 | 2018-01-02 | 2026-07-10 | 0.4979 |

## Logistic 相对 Ridge 的半导体诊断

- 急跌：Ridge 跨折占优；可比较 4 折，Logistic 相对 Ridge 折次胜率 0.0%。 平衡准确率差 -0.055；下跌召回差 +0.033。
- 修正：Ridge 跨折占优；可比较 4 折，Logistic 相对 Ridge 折次胜率 25.0%。 平衡准确率差 -0.019；下跌召回差 +0.162。
- 震荡：Ridge 跨折占优；可比较 4 折，Logistic 相对 Ridge 折次胜率 25.0%。 平衡准确率差 -0.026；下跌召回差 +0.373。
- 市场承压：Ridge 跨折占优；可比较 4 折，Logistic 相对 Ridge 折次胜率 25.0%。 平衡准确率差 -0.011；下跌召回差 +0.300。
- 上涨趋势：Logistic 跨折占优；可比较 4 折，Logistic 相对 Ridge 折次胜率 75.0%。 平衡准确率差 +0.020；下跌召回差 +0.454。

## 分层指标

| scope | 股票组 | horizon | regime | 市场阶段 | sample_mode | specification | sample_count | balanced_accuracy | macro_f1 | down_precision | down_recall | up_precision | up_recall | return_mae | rank_ic | comparable_fold_count | fold_win_rate_vs_ridge_current | 证据状态 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | 全部 | 5.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 10720.0000 | 0.3367 | 0.2681 | 0.3205 | 0.0359 | 0.5557 | 0.9544 |  |  | 4.0000 | 0.2500 | 可比较 |
| all | 全部 | 5.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 10720.0000 | 0.3678 | 0.3477 | 0.3487 | 0.2256 | 0.5792 | 0.5516 | 0.0852 | 0.1229 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | correction | 修正 | overlapping | logistic_decay_market | 57645.0000 | 0.3466 | 0.3029 | 0.3914 | 0.1303 | 0.4762 | 0.8464 |  |  | 4.0000 | 0.5000 | 可比较 |
| all | 全部 | 5.0000 | correction | 修正 | overlapping | ridge_current | 57645.0000 | 0.3590 | 0.3364 | 0.4079 | 0.2807 | 0.5187 | 0.4162 | 0.0675 | 0.0897 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 32065.0000 | 0.3615 | 0.3602 | 0.3452 | 0.3035 | 0.4679 | 0.5081 |  |  | 4.0000 | 0.7500 | 可比较 |
| all | 全部 | 5.0000 | range_bound | 震荡 | overlapping | ridge_current | 32065.0000 | 0.3540 | 0.3205 | 0.3202 | 0.1778 | 0.4875 | 0.4392 | 0.0575 | -0.0029 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 81558.0000 | 0.3574 | 0.3335 | 0.4085 | 0.1750 | 0.4251 | 0.7183 |  |  | 4.0000 | 0.7500 | 可比较 |
| all | 全部 | 5.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 81558.0000 | 0.3439 | 0.3090 | 0.3782 | 0.2229 | 0.4391 | 0.3367 | 0.0615 | -0.0099 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 189147.0000 | 0.3894 | 0.3799 | 0.4203 | 0.3770 | 0.4251 | 0.3901 |  |  | 4.0000 | 1.0000 | 可比较 |
| all | 全部 | 5.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 189147.0000 | 0.3509 | 0.3255 | 0.3725 | 0.2731 | 0.4355 | 0.3169 | 0.0600 | -0.0102 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 2431.0000 | 0.3298 | 0.2440 | 0.2887 | 0.0309 | 0.4916 | 0.9429 |  |  | 3.0000 | 0.3333 | 可比较 |
| all | 全部 | 5.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 2431.0000 | 0.3954 | 0.3877 | 0.4855 | 0.3149 | 0.5526 | 0.6134 | 0.0799 | 0.2034 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 11353.0000 | 0.3468 | 0.3075 | 0.3799 | 0.1409 | 0.5014 | 0.8470 |  |  | 4.0000 | 0.2500 | 可比较 |
| all | 全部 | 5.0000 | correction | 修正 | non_overlapping | ridge_current | 11353.0000 | 0.3568 | 0.3332 | 0.3736 | 0.2536 | 0.5401 | 0.4275 | 0.0661 | 0.0978 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 6341.0000 | 0.3593 | 0.3561 | 0.3568 | 0.2888 | 0.4550 | 0.5191 |  |  | 4.0000 | 0.2500 | 可比较 |
| all | 全部 | 5.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 6341.0000 | 0.3602 | 0.3212 | 0.3421 | 0.1833 | 0.4633 | 0.4225 | 0.0590 | -0.0404 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 16997.0000 | 0.3591 | 0.3335 | 0.4233 | 0.1767 | 0.4193 | 0.7192 |  |  | 4.0000 | 0.7500 | 可比较 |
| all | 全部 | 5.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 16997.0000 | 0.3357 | 0.2979 | 0.3726 | 0.2097 | 0.4190 | 0.3267 | 0.0644 | -0.0364 | 0.0000 |  | 基线 |
| all | 全部 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 37311.0000 | 0.3954 | 0.3855 | 0.4270 | 0.3878 | 0.4364 | 0.3932 |  |  | 4.0000 | 1.0000 | 可比较 |
| all | 全部 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 37311.0000 | 0.3572 | 0.3317 | 0.3807 | 0.2866 | 0.4424 | 0.3188 | 0.0601 | -0.0023 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 10720.0000 | 0.3508 | 0.2857 | 0.5474 | 0.0760 | 0.5478 | 0.9633 |  |  | 4.0000 | 0.5000 | 可比较 |
| all | 全部 | 20.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 10720.0000 | 0.3794 | 0.3553 | 0.3451 | 0.1501 | 0.5771 | 0.7216 | 0.1655 | 0.1878 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | correction | 修正 | overlapping | logistic_decay_market | 57645.0000 | 0.3303 | 0.2511 | 0.2588 | 0.0325 | 0.5059 | 0.9382 |  |  | 4.0000 | 0.2500 | 可比较 |
| all | 全部 | 20.0000 | correction | 修正 | overlapping | ridge_current | 57645.0000 | 0.3586 | 0.3349 | 0.3455 | 0.1935 | 0.5421 | 0.5566 | 0.1442 | 0.0856 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 31825.0000 | 0.3514 | 0.3120 | 0.4252 | 0.1615 | 0.4264 | 0.7901 |  |  | 4.0000 | 0.5000 | 可比较 |
| all | 全部 | 20.0000 | range_bound | 震荡 | overlapping | ridge_current | 31825.0000 | 0.3425 | 0.3133 | 0.3761 | 0.1810 | 0.4217 | 0.5032 | 0.1325 | -0.0520 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 78678.0000 | 0.3413 | 0.3035 | 0.3577 | 0.1368 | 0.4580 | 0.8060 |  |  | 4.0000 | 0.2500 | 可比较 |
| all | 全部 | 20.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 78678.0000 | 0.3469 | 0.3232 | 0.3549 | 0.1972 | 0.4834 | 0.4670 | 0.1345 | 0.0362 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 188667.0000 | 0.3543 | 0.3431 | 0.3648 | 0.5455 | 0.4382 | 0.2806 |  |  | 4.0000 | 0.7500 | 可比较 |
| all | 全部 | 20.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 188667.0000 | 0.3352 | 0.3221 | 0.3375 | 0.3119 | 0.4746 | 0.3547 | 0.1429 | -0.0115 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 613.0000 | 0.3326 | 0.2345 | 0.2857 | 0.0094 | 0.5025 | 0.9774 |  |  | 2.0000 | 0.5000 | 可比较 |
| all | 全部 | 20.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 613.0000 | 0.3458 | 0.3458 | 0.3333 | 0.3099 | 0.5061 | 0.5387 | 0.1473 | 0.0423 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 2630.0000 | 0.3285 | 0.2647 | 0.2864 | 0.0655 | 0.4958 | 0.8937 |  |  | 4.0000 | 0.5000 | 可比较 |
| all | 全部 | 20.0000 | correction | 修正 | non_overlapping | ridge_current | 2630.0000 | 0.3790 | 0.3432 | 0.3875 | 0.1611 | 0.5446 | 0.5988 | 0.1341 | 0.1099 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 1591.0000 | 0.3822 | 0.3412 | 0.4867 | 0.1211 | 0.4896 | 0.8910 |  |  | 3.0000 | 0.6667 | 可比较 |
| all | 全部 | 20.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 1591.0000 | 0.3624 | 0.3350 | 0.4091 | 0.2388 | 0.4855 | 0.4280 | 0.1203 | 0.0444 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 5012.0000 | 0.3353 | 0.2989 | 0.3093 | 0.1254 | 0.4598 | 0.7943 |  |  | 4.0000 | 0.5000 | 可比较 |
| all | 全部 | 20.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 5012.0000 | 0.3453 | 0.3239 | 0.3385 | 0.1818 | 0.4764 | 0.4921 | 0.1403 | 0.0095 | 0.0000 |  | 基线 |
| all | 全部 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 8965.0000 | 0.3476 | 0.3388 | 0.3588 | 0.5324 | 0.4274 | 0.2819 |  |  | 4.0000 | 0.7500 | 可比较 |
| all | 全部 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 8965.0000 | 0.3220 | 0.3138 | 0.3371 | 0.3284 | 0.4347 | 0.3385 | 0.1464 | -0.0525 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 4195.0000 | 0.3276 | 0.2883 | 0.3181 | 0.1379 | 0.5231 | 0.8282 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 5.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 4195.0000 | 0.3602 | 0.1985 | 0.2500 | 0.0212 | 0.5779 | 0.2169 | 0.0773 | 0.0617 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | correction | 修正 | overlapping | logistic_decay_market | 21984.0000 | 0.3460 | 0.3231 | 0.3737 | 0.1860 | 0.4660 | 0.7500 |  |  | 4.0000 | 0.2500 | 可比较 |
| other | 其他 | 5.0000 | correction | 修正 | overlapping | ridge_current | 21984.0000 | 0.3642 | 0.2055 | 0.4795 | 0.0359 | 0.5055 | 0.1703 | 0.0515 | 0.0541 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 12208.0000 | 0.3829 | 0.3730 | 0.3782 | 0.2937 | 0.4668 | 0.4667 |  |  | 4.0000 | 1.0000 | 可比较 |
| other | 其他 | 5.0000 | range_bound | 震荡 | overlapping | ridge_current | 12208.0000 | 0.3600 | 0.2003 | 0.3448 | 0.0070 | 0.4554 | 0.1717 | 0.0421 | -0.0062 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 31080.0000 | 0.3616 | 0.3464 | 0.3946 | 0.2005 | 0.4197 | 0.6168 |  |  | 4.0000 | 0.7500 | 可比较 |
| other | 其他 | 5.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 31080.0000 | 0.3538 | 0.1847 | 0.3675 | 0.0165 | 0.4358 | 0.1149 | 0.0418 | -0.0168 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 73229.0000 | 0.3833 | 0.3646 | 0.3913 | 0.3300 | 0.4125 | 0.3273 |  |  | 4.0000 | 1.0000 | 可比较 |
| other | 其他 | 5.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 73229.0000 | 0.3523 | 0.1853 | 0.3593 | 0.0176 | 0.4346 | 0.0918 | 0.0402 | -0.0419 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 960.0000 | 0.2831 | 0.2346 | 0.2333 | 0.1180 | 0.4370 | 0.7312 |  |  | 3.0000 | 0.3333 | 可比较 |
| other | 其他 | 5.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 960.0000 | 0.3505 | 0.2161 | 0.4054 | 0.0421 | 0.4909 | 0.2323 | 0.0793 | 0.0565 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 4311.0000 | 0.3501 | 0.3281 | 0.3759 | 0.2001 | 0.4871 | 0.7607 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 5.0000 | correction | 修正 | non_overlapping | ridge_current | 4311.0000 | 0.3632 | 0.2082 | 0.5044 | 0.0380 | 0.5093 | 0.1599 | 0.0494 | 0.0678 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 2410.0000 | 0.3552 | 0.3411 | 0.3463 | 0.2494 | 0.4115 | 0.4223 |  |  | 4.0000 | 0.5000 | 可比较 |
| other | 其他 | 5.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 2410.0000 | 0.3667 | 0.2020 | 0.5000 | 0.0067 | 0.4379 | 0.1751 | 0.0445 | -0.0318 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 6481.0000 | 0.3615 | 0.3488 | 0.4157 | 0.2239 | 0.4207 | 0.6036 |  |  | 4.0000 | 0.5000 | 可比较 |
| other | 其他 | 5.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 6481.0000 | 0.3593 | 0.1883 | 0.4454 | 0.0210 | 0.4501 | 0.1204 | 0.0434 | -0.0089 | 0.0000 |  | 基线 |
| other | 其他 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 14453.0000 | 0.3867 | 0.3677 | 0.4008 | 0.3366 | 0.4176 | 0.3293 |  |  | 4.0000 | 1.0000 | 可比较 |
| other | 其他 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 14453.0000 | 0.3551 | 0.1873 | 0.3872 | 0.0191 | 0.4452 | 0.0936 | 0.0402 | -0.0352 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 4195.0000 | 0.3271 | 0.2765 | 0.3327 | 0.1185 | 0.5086 | 0.8537 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 20.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 4195.0000 | 0.4204 | 0.3182 | 0.3679 | 0.0266 | 0.5959 | 0.6461 | 0.1266 | 0.2130 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | correction | 修正 | overlapping | logistic_decay_market | 21984.0000 | 0.3280 | 0.2675 | 0.2779 | 0.0727 | 0.4967 | 0.8829 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 20.0000 | correction | 修正 | overlapping | ridge_current | 21984.0000 | 0.3804 | 0.2768 | 0.3061 | 0.0277 | 0.5274 | 0.4540 | 0.1039 | 0.0459 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 12119.0000 | 0.3640 | 0.3548 | 0.4234 | 0.2966 | 0.4347 | 0.6313 |  |  | 4.0000 | 0.5000 | 可比较 |
| other | 其他 | 20.0000 | range_bound | 震荡 | overlapping | ridge_current | 12119.0000 | 0.3753 | 0.2585 | 0.4476 | 0.0327 | 0.4304 | 0.3336 | 0.0854 | -0.0042 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 30012.0000 | 0.3413 | 0.3220 | 0.3205 | 0.1599 | 0.4513 | 0.7070 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 20.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 30012.0000 | 0.3635 | 0.2565 | 0.3057 | 0.0353 | 0.4659 | 0.3173 | 0.0870 | -0.0329 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 73051.0000 | 0.3531 | 0.3374 | 0.3325 | 0.4682 | 0.4399 | 0.2652 |  |  | 4.0000 | 0.5000 | 可比较 |
| other | 其他 | 20.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 73051.0000 | 0.3503 | 0.2273 | 0.2801 | 0.0619 | 0.4456 | 0.1724 | 0.0947 | -0.0557 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 242.0000 | 0.3349 | 0.2562 | 0.4167 | 0.0617 | 0.5088 | 0.9431 |  |  | 2.0000 | 0.5000 | 可比较 |
| other | 其他 | 20.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 242.0000 | 0.3199 | 0.2418 | 0.0000 | 0.0000 | 0.4532 | 0.5122 | 0.1063 | -0.1368 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 991.0000 | 0.3411 | 0.2895 | 0.4118 | 0.1160 | 0.4902 | 0.8724 |  |  | 4.0000 | 0.2500 | 可比较 |
| other | 其他 | 20.0000 | correction | 修正 | non_overlapping | ridge_current | 991.0000 | 0.3910 | 0.2698 | 0.1875 | 0.0083 | 0.5419 | 0.4794 | 0.0998 | 0.0572 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 601.0000 | 0.4305 | 0.4242 | 0.4779 | 0.2596 | 0.5078 | 0.7014 |  |  | 3.0000 | 0.3333 | 可比较 |
| other | 其他 | 20.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 601.0000 | 0.3965 | 0.2776 | 0.6429 | 0.0433 | 0.4785 | 0.3201 | 0.0754 | 0.0731 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 1922.0000 | 0.3270 | 0.3145 | 0.2598 | 0.1594 | 0.4438 | 0.6472 |  |  | 4.0000 | 0.0000 | 可比较 |
| other | 其他 | 20.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 1922.0000 | 0.3695 | 0.2705 | 0.3434 | 0.0511 | 0.4789 | 0.3191 | 0.0890 | 0.0069 | 0.0000 |  | 基线 |
| other | 其他 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 3477.0000 | 0.3626 | 0.3442 | 0.3462 | 0.5081 | 0.4426 | 0.2430 |  |  | 4.0000 | 0.5000 | 可比较 |
| other | 其他 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 3477.0000 | 0.3577 | 0.2351 | 0.3273 | 0.0734 | 0.4506 | 0.1711 | 0.0953 | -0.0411 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 2373.0000 | 0.3332 | 0.2728 | 0.2333 | 0.0368 | 0.5612 | 0.9336 |  |  | 4.0000 | 0.0000 | 可比较 |
| semiconductor | 半导体 | 5.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 2373.0000 | 0.3880 | 0.2854 | 0.2143 | 0.0039 | 0.5926 | 0.6254 | 0.0711 | 0.1452 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | correction | 修正 | overlapping | logistic_decay_market | 12548.0000 | 0.3429 | 0.3068 | 0.3879 | 0.1689 | 0.4906 | 0.8200 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 5.0000 | correction | 修正 | overlapping | ridge_current | 12548.0000 | 0.3617 | 0.2428 | 0.3474 | 0.0068 | 0.5210 | 0.4705 | 0.0614 | 0.0629 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 6974.0000 | 0.3287 | 0.3319 | 0.3446 | 0.3755 | 0.4565 | 0.4840 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 5.0000 | range_bound | 震荡 | overlapping | ridge_current | 6974.0000 | 0.3548 | 0.2125 | 0.2222 | 0.0030 | 0.4544 | 0.3294 | 0.0580 | -0.0695 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 17825.0000 | 0.3375 | 0.3246 | 0.4067 | 0.3044 | 0.4346 | 0.6396 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 5.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 17825.0000 | 0.3481 | 0.1773 | 0.2941 | 0.0047 | 0.4317 | 0.1988 | 0.0589 | -0.0460 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 41596.0000 | 0.3635 | 0.3635 | 0.4164 | 0.4665 | 0.4379 | 0.3995 |  |  | 4.0000 | 0.7500 | 可比较 |
| semiconductor | 半导体 | 5.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 41596.0000 | 0.3437 | 0.1646 | 0.3662 | 0.0122 | 0.4276 | 0.1273 | 0.0546 | -0.0270 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 542.0000 | 0.3296 | 0.2453 | 0.2667 | 0.0351 | 0.4596 | 0.9209 |  |  | 3.0000 | 0.3333 | 可比较 |
| semiconductor | 半导体 | 5.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 542.0000 | 0.4125 | 0.2866 | 0.4000 | 0.0088 | 0.5058 | 0.6877 | 0.0821 | 0.1211 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 2463.0000 | 0.3320 | 0.3015 | 0.3102 | 0.1584 | 0.5263 | 0.8055 |  |  | 4.0000 | 0.5000 | 可比较 |
| semiconductor | 半导体 | 5.0000 | correction | 修正 | non_overlapping | ridge_current | 2463.0000 | 0.3518 | 0.2427 | 0.1579 | 0.0035 | 0.5518 | 0.4571 | 0.0602 | 0.0509 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 1377.0000 | 0.3292 | 0.3339 | 0.3456 | 0.3431 | 0.4385 | 0.4938 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 5.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 1377.0000 | 0.3452 | 0.2037 | 0.2500 | 0.0036 | 0.4247 | 0.3169 | 0.0634 | -0.1331 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 3724.0000 | 0.3440 | 0.3303 | 0.4211 | 0.3107 | 0.4246 | 0.6449 |  |  | 4.0000 | 0.5000 | 可比较 |
| semiconductor | 半导体 | 5.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 3724.0000 | 0.3444 | 0.1744 | 0.3158 | 0.0037 | 0.4035 | 0.1892 | 0.0575 | -0.0700 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 8202.0000 | 0.3727 | 0.3720 | 0.4202 | 0.4758 | 0.4540 | 0.4023 |  |  | 4.0000 | 1.0000 | 可比较 |
| semiconductor | 半导体 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 8202.0000 | 0.3478 | 0.1642 | 0.3860 | 0.0131 | 0.4413 | 0.1295 | 0.0546 | -0.0094 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 2373.0000 | 0.3522 | 0.3305 | 0.3708 | 0.2558 | 0.5742 | 0.7875 |  |  | 4.0000 | 0.7500 | 可比较 |
| semiconductor | 半导体 | 20.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 2373.0000 | 0.3515 | 0.2866 | 0.5000 | 0.0068 | 0.5876 | 0.9433 | 0.1276 | 0.2356 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | correction | 修正 | overlapping | logistic_decay_market | 12548.0000 | 0.3195 | 0.2655 | 0.2486 | 0.0568 | 0.5127 | 0.8606 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 20.0000 | correction | 修正 | overlapping | ridge_current | 12548.0000 | 0.3384 | 0.2620 | 0.4578 | 0.0080 | 0.5255 | 0.9036 | 0.1420 | 0.0819 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 6922.0000 | 0.3651 | 0.3430 | 0.5265 | 0.2783 | 0.4676 | 0.7468 |  |  | 4.0000 | 0.5000 | 可比较 |
| semiconductor | 半导体 | 20.0000 | range_bound | 震荡 | overlapping | ridge_current | 6922.0000 | 0.3362 | 0.2504 | 0.4900 | 0.0163 | 0.4310 | 0.6936 | 0.1287 | -0.0307 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 17201.0000 | 0.3312 | 0.3099 | 0.3638 | 0.2241 | 0.4680 | 0.7140 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 20.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 17201.0000 | 0.3549 | 0.2617 | 0.2134 | 0.0098 | 0.4693 | 0.6106 | 0.1253 | -0.0228 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 41492.0000 | 0.3412 | 0.3252 | 0.3658 | 0.6010 | 0.4940 | 0.3507 |  |  | 4.0000 | 0.5000 | 可比较 |
| semiconductor | 半导体 | 20.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 41492.0000 | 0.3387 | 0.2366 | 0.3049 | 0.0829 | 0.5044 | 0.2583 | 0.1215 | -0.0144 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 138.0000 | 0.2116 | 0.1732 | 0.0333 | 0.0204 | 0.4135 | 0.6143 |  |  | 2.0000 | 0.0000 | 可比较 |
| semiconductor | 半导体 | 20.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 138.0000 | 0.3758 | 0.3191 | 1.0000 | 0.0408 | 0.5078 | 0.9286 | 0.1105 | -0.1732 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 565.0000 | 0.3248 | 0.2526 | 0.3125 | 0.0463 | 0.4855 | 0.8996 |  |  | 4.0000 | 0.1250 | 可比较 |
| semiconductor | 半导体 | 20.0000 | correction | 修正 | non_overlapping | ridge_current | 565.0000 | 0.3384 | 0.2483 | 1.0000 | 0.0046 | 0.5096 | 0.9534 | 0.1312 | 0.2097 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 345.0000 | 0.3806 | 0.3704 | 0.5161 | 0.2560 | 0.6047 | 0.8168 |  |  | 3.0000 | 1.0000 | 可比较 |
| semiconductor | 半导体 | 20.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 345.0000 | 0.3264 | 0.2423 | 0.7500 | 0.0240 | 0.4867 | 0.5759 | 0.1669 | -0.1459 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 1099.0000 | 0.3290 | 0.3125 | 0.3645 | 0.2568 | 0.4256 | 0.6478 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 20.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 1099.0000 | 0.3561 | 0.2637 | 0.2857 | 0.0136 | 0.4233 | 0.5493 | 0.1163 | -0.0701 | 0.0000 |  | 基线 |
| semiconductor | 半导体 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 1976.0000 | 0.3394 | 0.3234 | 0.3633 | 0.5878 | 0.4936 | 0.3649 |  |  | 4.0000 | 0.2500 | 可比较 |
| semiconductor | 半导体 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 1976.0000 | 0.3357 | 0.2451 | 0.3090 | 0.0980 | 0.4872 | 0.2608 | 0.1137 | -0.0291 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 4152.0000 | 0.3378 | 0.2680 | 0.3333 | 0.0237 | 0.5765 | 0.9699 |  |  | 4.0000 | 0.5000 | 可比较 |
| software | 软件 | 5.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 4152.0000 | 0.3615 | 0.3613 | 0.3459 | 0.3229 | 0.6064 | 0.6224 | 0.1136 | 0.1328 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | correction | 修正 | overlapping | logistic_decay_market | 23113.0000 | 0.3329 | 0.2737 | 0.3665 | 0.0974 | 0.4673 | 0.8664 |  |  | 4.0000 | 0.5000 | 可比较 |
| software | 软件 | 5.0000 | correction | 修正 | overlapping | ridge_current | 23113.0000 | 0.3469 | 0.3460 | 0.4081 | 0.4049 | 0.5002 | 0.4668 | 0.0953 | 0.0650 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 12883.0000 | 0.3353 | 0.3297 | 0.3125 | 0.2218 | 0.4703 | 0.5834 |  |  | 4.0000 | 0.5000 | 可比较 |
| software | 软件 | 5.0000 | range_bound | 震荡 | overlapping | ridge_current | 12883.0000 | 0.3445 | 0.3416 | 0.3365 | 0.2772 | 0.5027 | 0.5580 | 0.0814 | 0.0114 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 32653.0000 | 0.3511 | 0.3198 | 0.4324 | 0.1882 | 0.4256 | 0.7663 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 5.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 32653.0000 | 0.3276 | 0.3236 | 0.3765 | 0.3682 | 0.4161 | 0.3634 | 0.0844 | -0.0472 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 74322.0000 | 0.3844 | 0.3802 | 0.4438 | 0.3548 | 0.4248 | 0.4741 |  |  | 4.0000 | 1.0000 | 可比较 |
| software | 软件 | 5.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 74322.0000 | 0.3406 | 0.3390 | 0.3863 | 0.3686 | 0.4213 | 0.4026 | 0.0813 | -0.0131 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 929.0000 | 0.3378 | 0.2553 | 0.3333 | 0.0187 | 0.5295 | 0.9694 |  |  | 3.0000 | 0.6667 | 可比较 |
| software | 软件 | 5.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 929.0000 | 0.4105 | 0.4112 | 0.4906 | 0.4081 | 0.5923 | 0.6878 | 0.1015 | 0.2039 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 4579.0000 | 0.3271 | 0.2722 | 0.3301 | 0.0969 | 0.4868 | 0.8578 |  |  | 4.0000 | 0.2500 | 可比较 |
| software | 软件 | 5.0000 | correction | 修正 | non_overlapping | ridge_current | 4579.0000 | 0.3379 | 0.3368 | 0.3772 | 0.3685 | 0.5126 | 0.4785 | 0.0957 | 0.0626 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 2554.0000 | 0.3456 | 0.3384 | 0.3384 | 0.2297 | 0.4731 | 0.6097 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 5.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 2554.0000 | 0.3282 | 0.3230 | 0.3150 | 0.2443 | 0.4733 | 0.5404 | 0.0815 | -0.0523 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 6792.0000 | 0.3528 | 0.3180 | 0.4405 | 0.1827 | 0.4209 | 0.7746 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 5.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 6792.0000 | 0.3189 | 0.3146 | 0.3855 | 0.3643 | 0.3940 | 0.3522 | 0.0885 | -0.0633 | 0.0000 |  | 基线 |
| software | 软件 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 14656.0000 | 0.3913 | 0.3878 | 0.4505 | 0.3720 | 0.4390 | 0.4780 |  |  | 4.0000 | 1.0000 | 可比较 |
| software | 软件 | 5.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 14656.0000 | 0.3402 | 0.3384 | 0.3790 | 0.3676 | 0.4199 | 0.3966 | 0.0816 | -0.0120 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | acute_selloff | 急跌 | overlapping | logistic_decay_market | 4152.0000 | 0.3350 | 0.2631 | 0.3439 | 0.0374 | 0.5435 | 0.9519 |  |  | 4.0000 | 0.5000 | 可比较 |
| software | 软件 | 20.0000 | acute_selloff | 急跌 | overlapping | ridge_current | 4152.0000 | 0.3855 | 0.3834 | 0.4093 | 0.3100 | 0.6013 | 0.7136 | 0.2540 | 0.1834 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | correction | 修正 | overlapping | logistic_decay_market | 23113.0000 | 0.3258 | 0.2492 | 0.2856 | 0.0464 | 0.5038 | 0.9228 |  |  | 4.0000 | 0.2500 | 可比较 |
| software | 软件 | 20.0000 | correction | 修正 | overlapping | ridge_current | 23113.0000 | 0.3445 | 0.3442 | 0.3663 | 0.3778 | 0.5267 | 0.5047 | 0.2123 | 0.0566 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | range_bound | 震荡 | overlapping | logistic_decay_market | 12784.0000 | 0.3206 | 0.2499 | 0.3409 | 0.0919 | 0.4040 | 0.8365 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 20.0000 | range_bound | 震荡 | overlapping | ridge_current | 12784.0000 | 0.3161 | 0.3084 | 0.3840 | 0.2742 | 0.3955 | 0.5265 | 0.2023 | -0.0931 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | under_pressure | 市场承压 | overlapping | logistic_decay_market | 31465.0000 | 0.3382 | 0.2928 | 0.3977 | 0.1793 | 0.4575 | 0.8101 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 20.0000 | under_pressure | 市场承压 | overlapping | ridge_current | 31465.0000 | 0.3347 | 0.3343 | 0.3664 | 0.3696 | 0.4650 | 0.4462 | 0.1911 | 0.0088 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | uptrend | 上涨趋势 | overlapping | logistic_decay_market | 74124.0000 | 0.3397 | 0.3404 | 0.3911 | 0.4446 | 0.4223 | 0.4406 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 20.0000 | uptrend | 上涨趋势 | overlapping | ridge_current | 74124.0000 | 0.3326 | 0.3323 | 0.3774 | 0.4042 | 0.4494 | 0.4105 | 0.1995 | -0.0168 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | acute_selloff | 急跌 | non_overlapping | logistic_decay_market | 233.0000 | 0.3357 | 0.2373 | 1.0000 | 0.0241 | 0.5022 | 0.9829 |  |  | 2.0000 | 0.0000 | 可比较 |
| software | 软件 | 20.0000 | acute_selloff | 急跌 | non_overlapping | ridge_current | 233.0000 | 0.4757 | 0.4638 | 0.5327 | 0.6867 | 0.6726 | 0.6496 | 0.1987 | 0.3969 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | correction | 修正 | non_overlapping | logistic_decay_market | 1074.0000 | 0.2986 | 0.2391 | 0.1441 | 0.0443 | 0.4916 | 0.8360 |  |  | 4.0000 | 0.5000 | 可比较 |
| software | 软件 | 20.0000 | correction | 修正 | non_overlapping | ridge_current | 1074.0000 | 0.3308 | 0.3297 | 0.3515 | 0.3021 | 0.5228 | 0.5508 | 0.2002 | -0.0005 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | range_bound | 震荡 | non_overlapping | logistic_decay_market | 645.0000 | 0.3209 | 0.2472 | 0.3467 | 0.0963 | 0.4106 | 0.8467 |  |  | 3.0000 | 0.3333 | 可比较 |
| software | 软件 | 20.0000 | range_bound | 震荡 | non_overlapping | ridge_current | 645.0000 | 0.3577 | 0.3552 | 0.4495 | 0.3630 | 0.4598 | 0.5219 | 0.1569 | 0.0561 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | under_pressure | 市场承压 | non_overlapping | logistic_decay_market | 1991.0000 | 0.3228 | 0.2782 | 0.3133 | 0.1404 | 0.4772 | 0.8053 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 20.0000 | under_pressure | 市场承压 | non_overlapping | ridge_current | 1991.0000 | 0.3267 | 0.3262 | 0.3212 | 0.3262 | 0.4703 | 0.4539 | 0.2033 | -0.0411 | 0.0000 |  | 基线 |
| software | 软件 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | logistic_decay_market | 3512.0000 | 0.3202 | 0.3175 | 0.3742 | 0.4231 | 0.3976 | 0.4321 |  |  | 4.0000 | 0.7500 | 可比较 |
| software | 软件 | 20.0000 | uptrend | 上涨趋势 | non_overlapping | ridge_current | 3512.0000 | 0.3135 | 0.3131 | 0.3638 | 0.3962 | 0.4075 | 0.4032 | 0.2070 | -0.0451 | 0.0000 |  | 基线 |

## 判读限制

- `comparable_fold_count` 少于 2 的阶段标为证据不足，不据此判断模型优劣。
- 当前股票池和 SEC 分类仍存在幸存者偏差；阶段拆分不能消除该偏差。
- 阶段规则在查看本轮结果前已经冻结，不根据已知案例回调阈值。
