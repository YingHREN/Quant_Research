# 统一向下风险走步基准 v2

- 数据区间：2018-01-01 至 2026-07-20
- 股票数：240
- 完全相同的测试行：1148358
- 执行口径：观察日收盘生成信号，下一交易日开盘执行。
- 权限：研究结果不具备线上否决权。

## 晋级结论

- 冻结研究门槛通过，但仍只允许进入影子评估。
- 无失败原因。

## 同池核心结果

| scope | regime_scope | horizon | sample_mode | fold | specification | status | sample_count | precision | recall | specificity | balanced_accuracy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all | all | 5.0000 | non_overlapping | all | general_logistic_down | ok | 76939.0000 | 0.3960 | 0.3254 | 0.7631 | 0.5443 |
| all | all | 5.0000 | non_overlapping | all | immediate_8 | ok | 76939.0000 | 0.4306 | 0.0575 | 0.9637 | 0.5106 |
| all | all | 5.0000 | non_overlapping | all | memory_12 | ok | 76698.0000 | 0.3585 | 0.2366 | 0.7987 | 0.5176 |
| all | all | 5.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 30523.0000 | 0.5242 | 0.6195 | 0.6709 | 0.6452 |
| all | all | 5.0000 | non_overlapping | all | ridge_down | ok | 76939.0000 | 0.2746 | 0.2160 | 0.7277 | 0.4719 |
| all | all | 5.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 76199.0000 | 0.2945 | 0.4138 | 0.5312 | 0.4725 |
| all | all | 5.0000 | non_overlapping | all | toprisk_confirmed | ok | 76199.0000 | 0.4015 | 0.0302 | 0.9787 | 0.5045 |
| all | all | 5.0000 | non_overlapping | all | toprisk_stateful | ok | 76199.0000 | 0.3047 | 0.2729 | 0.7055 | 0.4892 |
| other | all | 5.0000 | non_overlapping | all | general_logistic_down | ok | 29686.0000 | 0.3438 | 0.3103 | 0.7892 | 0.5497 |
| other | all | 5.0000 | non_overlapping | all | immediate_8 | ok | 29686.0000 | 0.3746 | 0.0590 | 0.9649 | 0.5120 |
| other | all | 5.0000 | non_overlapping | all | memory_12 | ok | 29638.0000 | 0.2944 | 0.2395 | 0.7959 | 0.5177 |
| other | all | 5.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 11659.0000 | 0.5108 | 0.5058 | 0.7838 | 0.6448 |
| other | all | 5.0000 | non_overlapping | all | ridge_down | ok | 29686.0000 | 0.2236 | 0.2326 | 0.7125 | 0.4726 |
| other | all | 5.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 29542.0000 | 0.2388 | 0.4332 | 0.5106 | 0.4719 |
| other | all | 5.0000 | non_overlapping | all | toprisk_confirmed | ok | 29542.0000 | 0.3539 | 0.0296 | 0.9808 | 0.5052 |
| other | all | 5.0000 | non_overlapping | all | toprisk_stateful | ok | 29542.0000 | 0.2452 | 0.2790 | 0.6956 | 0.4873 |
| semiconductor | all | 5.0000 | non_overlapping | all | general_logistic_down | ok | 16877.0000 | 0.4315 | 0.3748 | 0.6849 | 0.5298 |
| semiconductor | all | 5.0000 | non_overlapping | all | immediate_8 | ok | 16877.0000 | 0.5058 | 0.0596 | 0.9628 | 0.5112 |
| semiconductor | all | 5.0000 | non_overlapping | all | memory_12 | ok | 16833.0000 | 0.4421 | 0.2321 | 0.8135 | 0.5228 |
| semiconductor | all | 5.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 6667.0000 | 0.5197 | 0.6893 | 0.5182 | 0.6037 |
| semiconductor | all | 5.0000 | non_overlapping | all | ridge_down | ok | 16877.0000 | 0.3509 | 0.2272 | 0.7317 | 0.4795 |
| semiconductor | all | 5.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 16745.0000 | 0.3769 | 0.4481 | 0.5291 | 0.4886 |
| semiconductor | all | 5.0000 | non_overlapping | all | toprisk_confirmed | ok | 16745.0000 | 0.4533 | 0.0350 | 0.9731 | 0.5041 |
| semiconductor | all | 5.0000 | non_overlapping | all | toprisk_stateful | ok | 16745.0000 | 0.3871 | 0.3081 | 0.6900 | 0.4991 |
| software_cloud | all | 5.0000 | non_overlapping | all | general_logistic_down | ok | 30376.0000 | 0.4173 | 0.3058 | 0.7750 | 0.5404 |
| software_cloud | all | 5.0000 | non_overlapping | all | immediate_8 | ok | 30376.0000 | 0.4385 | 0.0551 | 0.9628 | 0.5090 |
| software_cloud | all | 5.0000 | non_overlapping | all | memory_12 | ok | 30227.0000 | 0.3766 | 0.2372 | 0.7940 | 0.5156 |
| software_cloud | all | 5.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 12197.0000 | 0.5351 | 0.6628 | 0.6264 | 0.6446 |
| software_cloud | all | 5.0000 | non_overlapping | all | ridge_down | ok | 30376.0000 | 0.2869 | 0.1966 | 0.7425 | 0.4695 |
| software_cloud | all | 5.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 29912.0000 | 0.3059 | 0.3773 | 0.5550 | 0.4661 |
| software_cloud | all | 5.0000 | non_overlapping | all | toprisk_confirmed | ok | 29912.0000 | 0.4084 | 0.0277 | 0.9792 | 0.5034 |
| software_cloud | all | 5.0000 | non_overlapping | all | toprisk_stateful | ok | 29912.0000 | 0.3169 | 0.2458 | 0.7245 | 0.4852 |
| all | all | 10.0000 | non_overlapping | all | general_logistic_down | ok | 38454.0000 | 0.3324 | 0.2804 | 0.7569 | 0.5187 |
| all | all | 10.0000 | non_overlapping | all | immediate_8 | ok | 38454.0000 | 0.4065 | 0.0538 | 0.9661 | 0.5100 |
| all | all | 10.0000 | non_overlapping | all | memory_12 | ok | 38333.0000 | 0.3473 | 0.2408 | 0.8052 | 0.5230 |
| all | all | 10.0000 | non_overlapping | all | pressure_downside_logistic_v1 | insufficient | 0.0000 |  |  |  |  |
| all | all | 10.0000 | non_overlapping | all | ridge_down | ok | 38454.0000 | 0.2394 | 0.2264 | 0.6896 | 0.4580 |
| all | all | 10.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 38083.0000 | 0.2686 | 0.4256 | 0.5041 | 0.4649 |
| all | all | 10.0000 | non_overlapping | all | toprisk_confirmed | ok | 38083.0000 | 0.3972 | 0.0301 | 0.9804 | 0.5053 |
| all | all | 10.0000 | non_overlapping | all | toprisk_stateful | ok | 38083.0000 | 0.2896 | 0.2766 | 0.7097 | 0.4931 |
| other | all | 10.0000 | non_overlapping | all | general_logistic_down | ok | 14839.0000 | 0.2837 | 0.2713 | 0.7793 | 0.5253 |
| other | all | 10.0000 | non_overlapping | all | immediate_8 | ok | 14839.0000 | 0.3422 | 0.0537 | 0.9668 | 0.5102 |
| other | all | 10.0000 | non_overlapping | all | memory_12 | ok | 14815.0000 | 0.2812 | 0.2400 | 0.8024 | 0.5212 |
| other | all | 10.0000 | non_overlapping | all | pressure_downside_logistic_v1 | insufficient | 0.0000 |  |  |  |  |
| other | all | 10.0000 | non_overlapping | all | ridge_down | ok | 14839.0000 | 0.1974 | 0.2597 | 0.6599 | 0.4598 |
| other | all | 10.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 14767.0000 | 0.2187 | 0.4608 | 0.4719 | 0.4664 |
| other | all | 10.0000 | non_overlapping | all | toprisk_confirmed | ok | 14767.0000 | 0.3365 | 0.0296 | 0.9813 | 0.5054 |
| other | all | 10.0000 | non_overlapping | all | toprisk_stateful | ok | 14767.0000 | 0.2401 | 0.2930 | 0.7024 | 0.4977 |
| semiconductor | all | 10.0000 | non_overlapping | all | general_logistic_down | ok | 8434.0000 | 0.3548 | 0.3188 | 0.6837 | 0.5012 |
| semiconductor | all | 10.0000 | non_overlapping | all | immediate_8 | ok | 8434.0000 | 0.4478 | 0.0504 | 0.9661 | 0.5082 |
| semiconductor | all | 10.0000 | non_overlapping | all | memory_12 | ok | 8412.0000 | 0.4233 | 0.2305 | 0.8288 | 0.5296 |
| semiconductor | all | 10.0000 | non_overlapping | all | pressure_downside_logistic_v1 | insufficient | 0.0000 |  |  |  |  |
| semiconductor | all | 10.0000 | non_overlapping | all | ridge_down | ok | 8434.0000 | 0.3015 | 0.2301 | 0.7092 | 0.4696 |
| semiconductor | all | 10.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 8368.0000 | 0.3388 | 0.4574 | 0.5152 | 0.4863 |
| semiconductor | all | 10.0000 | non_overlapping | all | toprisk_confirmed | ok | 8368.0000 | 0.4742 | 0.0343 | 0.9793 | 0.5068 |
| semiconductor | all | 10.0000 | non_overlapping | all | toprisk_stateful | ok | 8368.0000 | 0.3600 | 0.3138 | 0.6970 | 0.5054 |
| software_cloud | all | 10.0000 | non_overlapping | all | general_logistic_down | ok | 15181.0000 | 0.3622 | 0.2642 | 0.7715 | 0.5179 |
| software_cloud | all | 10.0000 | non_overlapping | all | immediate_8 | ok | 15181.0000 | 0.4423 | 0.0560 | 0.9653 | 0.5107 |
| software_cloud | all | 10.0000 | non_overlapping | all | memory_12 | ok | 15106.0000 | 0.3718 | 0.2476 | 0.7955 | 0.5216 |
| software_cloud | all | 10.0000 | non_overlapping | all | pressure_downside_logistic_v1 | insufficient | 0.0000 |  |  |  |  |
| software_cloud | all | 10.0000 | non_overlapping | all | ridge_down | ok | 15181.0000 | 0.2544 | 0.2002 | 0.7118 | 0.4560 |
| software_cloud | all | 10.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 14948.0000 | 0.2836 | 0.3805 | 0.5340 | 0.4573 |
| software_cloud | all | 10.0000 | non_overlapping | all | toprisk_confirmed | ok | 14948.0000 | 0.4053 | 0.0281 | 0.9800 | 0.5041 |
| software_cloud | all | 10.0000 | non_overlapping | all | toprisk_stateful | ok | 14948.0000 | 0.2987 | 0.2420 | 0.7246 | 0.4833 |
| all | all | 20.0000 | non_overlapping | all | general_logistic_down | ok | 19125.0000 | 0.3154 | 0.2940 | 0.7098 | 0.5019 |
| all | all | 20.0000 | non_overlapping | all | immediate_8 | ok | 19125.0000 | 0.3646 | 0.0513 | 0.9593 | 0.5053 |
| all | all | 20.0000 | non_overlapping | all | memory_12 | ok | 19064.0000 | 0.3419 | 0.2404 | 0.7902 | 0.5153 |
| all | all | 20.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 8496.0000 | 0.4572 | 0.5550 | 0.6420 | 0.5985 |
| all | all | 20.0000 | non_overlapping | all | ridge_down | ok | 19125.0000 | 0.2291 | 0.1862 | 0.7150 | 0.4506 |
| all | all | 20.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 18938.0000 | 0.2656 | 0.3927 | 0.5102 | 0.4515 |
| all | all | 20.0000 | non_overlapping | all | toprisk_confirmed | ok | 18938.0000 | 0.3652 | 0.0292 | 0.9771 | 0.5032 |
| all | all | 20.0000 | non_overlapping | all | toprisk_stateful | ok | 18938.0000 | 0.2844 | 0.2669 | 0.6971 | 0.4820 |
| other | all | 20.0000 | non_overlapping | all | general_logistic_down | ok | 7378.0000 | 0.2620 | 0.3052 | 0.7073 | 0.5063 |
| other | all | 20.0000 | non_overlapping | all | immediate_8 | ok | 7378.0000 | 0.2816 | 0.0464 | 0.9597 | 0.5030 |
| other | all | 20.0000 | non_overlapping | all | memory_12 | ok | 7366.0000 | 0.2777 | 0.2396 | 0.7880 | 0.5138 |
| other | all | 20.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 3289.0000 | 0.4067 | 0.4205 | 0.7416 | 0.5810 |
| other | all | 20.0000 | non_overlapping | all | ridge_down | ok | 7378.0000 | 0.1902 | 0.2193 | 0.6820 | 0.4507 |
| other | all | 20.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 7342.0000 | 0.2218 | 0.4358 | 0.4809 | 0.4584 |
| other | all | 20.0000 | non_overlapping | all | toprisk_confirmed | ok | 7342.0000 | 0.3082 | 0.0263 | 0.9799 | 0.5031 |
| other | all | 20.0000 | non_overlapping | all | toprisk_stateful | ok | 7342.0000 | 0.2398 | 0.2816 | 0.6970 | 0.4893 |
| semiconductor | all | 20.0000 | non_overlapping | all | general_logistic_down | ok | 4195.0000 | 0.3713 | 0.3293 | 0.6916 | 0.5105 |
| semiconductor | all | 20.0000 | non_overlapping | all | immediate_8 | ok | 4195.0000 | 0.4339 | 0.0549 | 0.9604 | 0.5076 |
| semiconductor | all | 20.0000 | non_overlapping | all | memory_12 | ok | 4184.0000 | 0.3809 | 0.2275 | 0.7955 | 0.5115 |
| semiconductor | all | 20.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 1856.0000 | 0.4530 | 0.6482 | 0.5018 | 0.5750 |
| semiconductor | all | 20.0000 | non_overlapping | all | ridge_down | ok | 4195.0000 | 0.2939 | 0.1928 | 0.7438 | 0.4683 |
| semiconductor | all | 20.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 4162.0000 | 0.3233 | 0.4192 | 0.5162 | 0.4677 |
| semiconductor | all | 20.0000 | non_overlapping | all | toprisk_confirmed | ok | 4162.0000 | 0.4307 | 0.0399 | 0.9709 | 0.5054 |
| semiconductor | all | 20.0000 | non_overlapping | all | toprisk_stateful | ok | 4162.0000 | 0.3341 | 0.2968 | 0.6739 | 0.4853 |
| software_cloud | all | 20.0000 | non_overlapping | all | general_logistic_down | ok | 7552.0000 | 0.3361 | 0.2658 | 0.7225 | 0.4942 |
| software_cloud | all | 20.0000 | non_overlapping | all | immediate_8 | ok | 7552.0000 | 0.4012 | 0.0529 | 0.9583 | 0.5056 |
| software_cloud | all | 20.0000 | non_overlapping | all | memory_12 | ok | 7514.0000 | 0.3830 | 0.2485 | 0.7897 | 0.5191 |
| software_cloud | all | 20.0000 | non_overlapping | all | pressure_downside_logistic_v1 | ok | 3351.0000 | 0.4918 | 0.6043 | 0.6072 | 0.6058 |
| software_cloud | all | 20.0000 | non_overlapping | all | ridge_down | ok | 7552.0000 | 0.2410 | 0.1586 | 0.7361 | 0.4473 |
| software_cloud | all | 20.0000 | non_overlapping | all | ridge_plus_toprisk | ok | 7434.0000 | 0.2815 | 0.3459 | 0.5398 | 0.4428 |
| software_cloud | all | 20.0000 | non_overlapping | all | toprisk_confirmed | ok | 7434.0000 | 0.3657 | 0.0251 | 0.9773 | 0.5012 |
| software_cloud | all | 20.0000 | non_overlapping | all | toprisk_stateful | ok | 7434.0000 | 0.3002 | 0.2387 | 0.7100 | 0.4744 |

## 分层说明

- `semiconductor`：半导体。
- `software_cloud`：软件与云服务。
- `unclassified`：观察日没有可用点时分类，不并入其他组。

## 限制

- 二元规则分数不是概率，未伪造 ROC/PR AUC。
- 本报告不修改 Ridge、TOPRISK 或 forecast_decision_policy。
