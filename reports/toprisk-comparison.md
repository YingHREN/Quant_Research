# TOPRISK 统一比较报告

- 报告版本：`toprisk_comparison_v1`
- 样本区间：2016-07-26 至 2026-07-24
- 不利波动阈值：-5.00%

## 结果

| Group | Horizon | Signal | Status | N | Signals | Precision | Recall | Balanced accuracy | Mean MAE | Lead |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|
| all | 5 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| all | 5 | immediate_8 | available | 34219 | 1509 | 0.3711 | 0.0511 | 0.5051 | -0.0448 | 3.4018 |
| all | 5 | memory_12 | available | 18145 | 3775 | 0.4140 | 0.2342 | 0.5207 | -0.0519 | 3.5048 |
| all | 5 | toprisk_confirmed | available | 31939 | 816 | 0.3725 | 0.0295 | 0.5029 | -0.0458 | 3.5099 |
| all | 5 | toprisk_stateful | available | 31939 | 10252 | 0.3044 | 0.3032 | 0.4869 | -0.0412 | 3.7277 |
| all | 5 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 5 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 5 | immediate_8 | available | 20944 | 884 | 0.3495 | 0.0452 | 0.5022 | -0.0430 | 3.4337 |
| semiconductor | 5 | memory_12 | available | 10448 | 1893 | 0.4205 | 0.1973 | 0.5131 | -0.0525 | 3.3191 |
| semiconductor | 5 | toprisk_confirmed | available | 19624 | 554 | 0.3773 | 0.0326 | 0.5033 | -0.0451 | 3.5311 |
| semiconductor | 5 | toprisk_stateful | available | 19624 | 6909 | 0.3135 | 0.3381 | 0.4896 | -0.0417 | 3.6625 |
| semiconductor | 5 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| software | 5 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| software | 5 | immediate_8 | available | 13275 | 625 | 0.4016 | 0.0609 | 0.5100 | -0.0473 | 3.3625 |
| software | 5 | memory_12 | available | 7697 | 1882 | 0.4075 | 0.2904 | 0.5349 | -0.0514 | 3.6975 |
| software | 5 | toprisk_confirmed | available | 12315 | 262 | 0.3626 | 0.0244 | 0.5023 | -0.0474 | 3.4632 |
| software | 5 | toprisk_stateful | available | 12315 | 3343 | 0.2857 | 0.2457 | 0.4812 | -0.0402 | 3.8754 |
| software | 5 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | immediate_8 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | memory_12 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | toprisk_confirmed | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | toprisk_stateful | unavailable | 0 | 0 | — | — | — | — | — |
| other | 5 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| all | 10 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| all | 10 | immediate_8 | available | 34029 | 1505 | 0.4771 | 0.0464 | 0.5020 | -0.0609 | 5.9471 |
| all | 10 | memory_12 | available | 17955 | 3704 | 0.5435 | 0.2191 | 0.5131 | -0.0725 | 6.2931 |
| all | 10 | toprisk_confirmed | available | 31749 | 815 | 0.4798 | 0.0270 | 0.5012 | -0.0638 | 6.4706 |
| all | 10 | toprisk_stateful | available | 31749 | 10179 | 0.4321 | 0.3032 | 0.4840 | -0.0578 | 6.6935 |
| all | 10 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 10 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 10 | immediate_8 | available | 20834 | 883 | 0.4530 | 0.0422 | 0.4998 | -0.0581 | 6.0000 |
| semiconductor | 10 | memory_12 | available | 10338 | 1826 | 0.5110 | 0.1730 | 0.4962 | -0.0686 | 5.8510 |
| semiconductor | 10 | toprisk_confirmed | available | 19514 | 553 | 0.4684 | 0.0292 | 0.5008 | -0.0615 | 6.3012 |
| semiconductor | 10 | toprisk_stateful | available | 19514 | 6858 | 0.4374 | 0.3380 | 0.4877 | -0.0580 | 6.5763 |
| semiconductor | 10 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| software | 10 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| software | 10 | immediate_8 | available | 13195 | 622 | 0.5113 | 0.0531 | 0.5055 | -0.0648 | 5.8805 |
| software | 10 | memory_12 | available | 7617 | 1878 | 0.5751 | 0.2847 | 0.5380 | -0.0762 | 6.6750 |
| software | 10 | toprisk_confirmed | available | 12235 | 262 | 0.5038 | 0.0234 | 0.5019 | -0.0687 | 6.8030 |
| software | 10 | toprisk_stateful | available | 12235 | 3321 | 0.4210 | 0.2483 | 0.4786 | -0.0573 | 6.9449 |
| software | 10 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | immediate_8 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | memory_12 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | toprisk_confirmed | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | toprisk_stateful | unavailable | 0 | 0 | — | — | — | — | — |
| other | 10 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| all | 20 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| all | 20 | immediate_8 | available | 33649 | 1488 | 0.5874 | 0.0449 | 0.5007 | -0.0828 | 11.1213 |
| all | 20 | memory_12 | available | 17575 | 3626 | 0.6798 | 0.2207 | 0.5197 | -0.1012 | 11.9615 |
| all | 20 | toprisk_confirmed | available | 31369 | 789 | 0.5932 | 0.0257 | 0.5006 | -0.0839 | 11.2991 |
| all | 20 | toprisk_stateful | available | 31369 | 10036 | 0.5653 | 0.3111 | 0.4894 | -0.0805 | 12.3332 |
| all | 20 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 20 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| semiconductor | 20 | immediate_8 | available | 20614 | 870 | 0.5506 | 0.0403 | 0.4978 | -0.0748 | 10.6284 |
| semiconductor | 20 | memory_12 | available | 10118 | 1767 | 0.6406 | 0.1756 | 0.5013 | -0.0930 | 11.3180 |
| semiconductor | 20 | toprisk_confirmed | available | 19294 | 527 | 0.5693 | 0.0271 | 0.4997 | -0.0799 | 11.2500 |
| semiconductor | 20 | toprisk_stateful | available | 19294 | 6718 | 0.5665 | 0.3434 | 0.4943 | -0.0802 | 12.1159 |
| semiconductor | 20 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| software | 20 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| software | 20 | immediate_8 | available | 13035 | 618 | 0.6392 | 0.0519 | 0.5054 | -0.0941 | 11.7190 |
| software | 20 | memory_12 | available | 7457 | 1859 | 0.7171 | 0.2822 | 0.5449 | -0.1090 | 12.5079 |
| software | 20 | toprisk_confirmed | available | 12075 | 262 | 0.6412 | 0.0235 | 0.5022 | -0.0918 | 11.3869 |
| software | 20 | toprisk_stateful | available | 12075 | 3318 | 0.5627 | 0.2610 | 0.4831 | -0.0813 | 12.7761 |
| software | 20 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | ridge_down | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | immediate_8 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | memory_12 | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | toprisk_confirmed | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | toprisk_stateful | unavailable | 0 | 0 | — | — | — | — | — |
| other | 20 | ridge_plus_toprisk | unavailable | 0 | 0 | — | — | — | — | — |

## 局限

- Signals are evaluated point-in-time against future path outcomes.
- Market-regime labels are unavailable in this report.
- Ridge historical forecasts are unavailable; Ridge-derived comparisons are marked unavailable.
