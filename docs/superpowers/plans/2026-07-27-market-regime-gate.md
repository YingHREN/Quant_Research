# CAN SLIM 大盘环境门槛 v1 实施计划

1. 新建 `research/market_gate.py` 与因果状态机测试。
2. 覆盖确认上涨、承压、调整、反弹尝试、跟进日、派发日过期/消除和未来数据不变性。
3. 将门槛接入 `UniverseSnapshotService` 和 `MarketOverviewService`。
4. 将逐日门槛接入个股图表日期及模型输出注册表。
5. 增加股票池、个股标题和市场页的中英文状态与解释。
6. 用真实 SPY/QQQ 和 196 只主动池回放 2026 年 6～7 月，记录状态变化。
7. 跑完整测试、更新 `docs/modeling-todo.md`、合入 main 并重启服务。
