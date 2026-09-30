# 第十三轮：Bi-temporal 版本治理 + Agentic Memory 主动组织

参考 Graphiti（bi-temporal：valid_at/invalid_at + transaction time，时间旅行查询、矛盾解决）与 A-MEM（Zettelkasten 自组织：attributes、interlinking、continuous evolution）。

## 变更
- `memories` 新增 `valid_at/invalid_at/attributes_json/entities_json/provenance_json`；`add_memory`/`correct` 后自动组织 attributes/entities/provenance。
- `temporal_invalidate(old,new)`：supersede/correct 不再简单覆盖，旧版本打 `invalid_at`，新版本设 `valid_at`，支持 `GET /api/memories/time-travel?as_of=...`（"2025年3月时用户偏好是什么"）。
- 新增 `memory_links`（from/to/relation/weight）+ `auto_link`：写入时按 entity overlap + lexical 相似自动交叉链接；`GET /api/memories/{id}/neighbors`；`retrieve()` 融合一跳邻居加权，图遍历 + 时间过滤。
- 新增 `memory_evolutions` + `POST /api/memories/{id}/evolve`：持续 refinement 而非一次写入；演化链可审计。
- 兼容：旧库自动 ALTER 迁移；`status` 保留，`valid_at` 缺省回填 `created_at`。

## API
- `GET /api/memories/time-travel?as_of=<ISO>&q=&limit=`
- `GET /api/memories/{id}/neighbors?depth=1..3`
- `POST /api/memories/{id}/links/{other_id}?relation=related`
- `POST /api/memories/{id}/evolve {"new_info":..,"reason":..}`
- `GET /api/memories/{id}/evolutions`
- `retrieve(query,limit,kinds,as_of,expand_graph)` 同样支持 `as_of`。

## 验证
`pytest tests/test_core.py -q` 全过；手动：创建偏好→supersede→time_travel 查旧时刻返回旧版本；evolve 产生新版本 + evolution 记录；neighbors 非空。
