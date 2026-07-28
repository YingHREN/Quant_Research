# 接近枢轴图表标记设计

## 目标

在历史 K 线图中标出严格 VCP 首次从其他阶段进入 `near_pivot` 阶段的交易日，
让用户能看到“接近枢轴”何时发生，而不把连续多日状态重复画满图表。

## 定义

`near_pivot` 继续使用现有严格 VCP 定义：

- 当日严格 VCP 检测通过；
- 当日收盘价距离严格 VCP 枢轴为 `-5%` 至 `0%`。

新增布尔字段 `strict_vcp_near_pivot_start`。只有当前交易日
`strict_vcp_stage == "near_pivot"` 且前一交易日不是该阶段时为 `true`。
离开后再次进入可以产生新的标记。

## 数据流与界面

`research.entry_signals.build_entry_signal_rows` 生成状态跃迁字段；`web.app`
把真值转换为 `strict_vcp_near_pivot_start` annotation。图表复用现有
`strict_vcp` 图层，新增独立的蓝色向上标记和本地化文案“进入接近枢轴区”。
因此现有“严格 VCP 形成”复选框可同时控制首次形成和接近枢轴两个标记，
不增加第十二个图层。

标记悬停详情继续使用该交易日已有的严格 VCP 枢轴与距枢轴百分比字段；
不会修改形态检测、枢轴水平线、突破确认或预测模型。

## 兼容与降级

旧缓存或旧图表行没有新字段时不生成标记，不根据当前状态补造历史日期。
连续多日处于 `near_pivot` 只标第一日，减少遮挡。

## 验证

- Python 单元测试覆盖首次进入、连续保持、退出后再次进入；
- API annotation 测试确认类型、日期和文案；
- JavaScript 测试确认样式、图层归属、优先级和中文/英文文案；
- 相关 Python、Node 图表测试与语法检查通过。
