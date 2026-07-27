# 退市证券身份与历史行业覆盖率

本报告只评估身份链接与点时 SIC 可恢复性；不执行价格回填，也不改变模型或 UI。

## 总览

- 固定样本：275
- 已确认：0
- 需复核：0
- 已拒绝：0
- 未解决：275
- 确认率：0.0%
- 实验门槛结论：`provider_access_blocked`
- 供应商请求状态：`{"authorization_error": 275}`
- SEC 采集策略：`targeted`

## 证据边界

- 当前 ticker 或名称相似本身不能确认历史身份。
- EODHD 行业字段只作为当前快照，不回填历史分类。
- SIC 仅从 SEC 文件头提取，并按可获得时间生效。
- 原始供应商响应、认证 URL 和 API 密钥不进入报告。
- 当门槛结论为 `provider_access_blocked` 时，覆盖率与全量成本投影不能用于决定回填。

## 完整性

- Catalog SHA-256：`fa432000e8f77577175d2b13590dd0be65d44fef5ceceed433f3d33752ad80b0`
- Sample SHA-256：`4a09895cef803deb0c69f01437b9dbc5393bba3946f45068b13203ce7dab7845`
- Reference DB：`{"foreign_key_errors": 0, "integrity_check": "ok"}`
- 受保护价格数据库 SHA-256：
  - `delisted_research_prices.db`：`b91f85ddf7ecb8c58220cda4b23879982d413292d524651933b4e8e20f340782`
  - `prices.db`：`2257c7671ac9dd5d34858793d8400f1c6b4c9849b1613d93220df42f91ecf093`
  - `research_prices.db`：`5ba4bcee280008eeac051f36341f07ca8753fadc467a037d0fe6b1251d15aba2`
- 规则版本：`{"identity": "delisted_identity_adjudication_v1", "sic_interval": "sec_sic_interval_v1", "sic_parser": "sec_submission_header_sic_v1"}`

