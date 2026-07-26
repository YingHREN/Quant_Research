# 独立研究价格库与交易行为板块实施计划

1. 先为价格复权、历史切段和幂等导入编写失败测试。
2. 实现 `data/research_store.py` 与建库脚本，导入证券身份、日线、公司行动和股票池成员。
3. 为点时 ETF 行为分类编写失败测试。
4. 实现 `data/market_behavior.py`，写入 `market_behavior_v1` 分类与 SEC 冲突说明。
5. 补齐扩充池拆股和分红缓存，构建 `data/research_prices.db`。
6. 运行单元测试、数据库完整性检查、行数/日期/切段/分类覆盖率审计。
7. 更新中文全局 TODO 和数据规模记录，提交到 `main`。
