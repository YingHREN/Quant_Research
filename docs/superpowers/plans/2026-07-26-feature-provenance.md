# 点时特征来源与版本审计实现计划

1. 新增不可变 `FeatureProvenanceRegistry`、特征定义和快照合约；先写失败测试覆盖排序、重复项、时区和时间边界。
2. 为 Ridge v4 注册全部输入特征及来源、时点语义、执行时点。
3. ForecastService 使用现有市场快照哈希生成 data version，把顶层目录和逐日期快照写入响应。
4. 模型输出面板渲染数据时点、截止日、特征版本和快照短版本；增加中英文文案和旧响应降级。
5. 更新全局 TODO，运行 Python/JavaScript 完整回归，合入 main 并重启本地服务。
