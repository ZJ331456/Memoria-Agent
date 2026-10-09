# Memory：个人分层记忆与治理

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [共享治理](../../docs/记忆治理与多Agent共享记忆.md)

本模块服务**个人对话运行时**。共享 Agent 空间另由 [../governance.py](../governance.py) 管理，尚未接入 `MemoryEngine.retrieve` 或个人 `memorize` 工具。

## 分层总览

参考用户提供的 *Agent Memory - The 5-Layer Playbook*，并核对 CoALA、MemGPT、LangGraph、Letta 等一手资料，采用以下职责分工。**遗忘是跨层治理机制**，不是再复制一份记忆。完整中文解释、PDF 思想改写和来源核验见[记忆分层 README](../../docs/memory-layers/README.md)。

| 层 | 本项目的实现 | 读取与治理 |
| --- | --- | --- |
| 工作记忆 | 最近消息、活动约束、增量摘要与本轮 context frame | 首条系统指令完整保留；最新用户请求和分层帧优先；全局及分区字符预算 |
| 情景记忆 | `memory_episodes` 中的短任务、执行状态、结果、工具名与来源消息/trace ID | 中文二元词组/英文词元检索，默认 top 3；完成状态不等于验证成功 |
| 语义记忆 | `memories` 的事实、偏好、目标、关系与有效版本 | 自动提取先审核；关键词/BM25/可选向量召回；纠正和替代可追溯 |
| 程序性记忆 | 人工维护的 `skills/*/SKILL.md` | 按任务选择和按需加载；SHA-256 指纹进入 trace；不自动把经历晋升为技能 |
| 遗忘与治理 | 情景 TTL/容量归档、事实版本状态、共享 ACL/过期/撤销 | 先过滤状态再召回；固定保留保护情景；来源删除与事实删除分别处理 |

## 文件映射

| 文件 | 职责 |
|---|---|
| [planner.py](planner.py) | `MemoryQueryPlanner` 门控、可选 fast 模型改写查询/类型/数量 |
| [engine.py](engine.py) | `MemoryEngine` 写入去重、替代、纠正、关键词/BM25/可选向量检索、图扩展和时间查询 |
| [embedding.py](embedding.py) | OpenAI-compatible `/embeddings` 请求、分批、超时及有限重试 |
| [worker.py](worker.py) | 会话结束后领取持久任务，提取候选并写入审核队列 |
| [episodic.py](episodic.py) | `EpisodicMemory` 记录真实来源任务、相关性召回、固定保留、归档和事件 |
| [forgetting.py](forgetting.py) | 后台情景 TTL/容量维护；取消后再关闭数据库 |
| [layer_settings.py](layer_settings.py) | `[memory.layers]` 平面配置的字段、类型与范围校验 |
| [budget.py](budget.py) | 本轮记忆读取工具次数和累计返回字符配额，计入初始上下文帧 |
| [layer.py](layer.py)、[markdown.py](markdown.py) | `MEMORY.md` 投影、`SELF.md` 和历史 `PENDING.md` 文件 |
| [../store.py](../store.py)、[../vector_index.py](../vector_index.py) | SQLite、FTS、来源操作账本、可选 sqlite-vec |

## 自动提取与人工审核

`AgentRuntime` 保存回复后，以用户消息 ID 为 `source_ref` 投递 `memory_jobs`。`MemoryJobWorker` 使用租约与 heartbeat 领取任务，调用 [LLMClient](../llm.py) 提取候选，存入 `memory_reviews`。**任务完成只表示提取完成，候选尚未生效**；未批准内容不参与检索或 prompt。

用户通过 `GET /api/memory-reviews` 查看候选与来源 ID，再用消息来源接口核对原始对话，可编辑正文、类型和重要度后调用 `POST /api/memory-reviews/{id}/approve`，或拒绝。批准才调用 `MemoryEngine.remember`；审核状态在 `pending → applying → approved` 间推进，失败或重启时恢复待审。`GET /api/messages/{id}/source` 与带 `anchor_id` 的会话消息接口定位来源。撤销已批准来源可先用 `POST /api/memories/undo` 的 dry-run 预览。详见[审核与来源定位](../../docs/自动记忆审核与来源定位.md)。

## 写入、检索和版本

手动 API 与个人 `memorize` 工具直接调用 `remember`；规范化正文完全相同的记忆会强化。其他候选按文本/向量相似度交给 fast 模型选择创建、强化或替代；没有决策器时，对部分可变类型有规则式矛盾替代。旧版本与原因保存在替代表；用户纠正产生新版本，可查看时间线。部分写入会生成实体/关键词属性与相关链接；`valid_at/invalid_at` 支持时间查询。个人记忆的 `source_ref` 和操作账本用于来源追踪及撤销。

`retrieve` 从 FTS/关键词种子和当前有效记忆取候选，清理问句尾词后计算词面分、BM25 分和可选向量相似度，按 RRF 融合；再小幅扩展相关记忆图并限制各记忆类型的数量。embedding 不可用时可降级为文本检索；`POST /api/memories/reindex` 可补向量。`MemoryQueryPlanner` 会跳过问候和无关短请求，必要时使用 fast 模型改写查询。检索和个人工具仍只面对个人 `memories` 表。

`MEMORY.md` 从有效结构化记忆重建，不是独立写入源；`SELF.md` 可作为 prompt 上下文。`PENDING.md` 是历史 Markdown 层及 Drift 摘要容器，**自动提取审核队列以 SQLite 为准**。

## 情景记录与来源生命周期

聊天轮次完成、失败或取消后记录简短情景；Drift 不生成自己的情景以避免反馈循环。任务最多 600 字符、结果最多 1200 字符、错误最多 300 字符，只存工具名称和消息/trace 指针，不存模型隐藏推理或原始工具参数。已有 trace 脱敏规则用于常见凭据，但不构成通用个人信息脱敏器。

情景默认 90 天过期，检索立即排除未固定的过期记录；后台按 TTL 与 1000 条活动容量软归档，固定记录受保护，全部固定时可以超过容量。管理视图能查看过期归档，召回工具不读取这些历史。维护 `dry_run` 只预览，归档事件保留原因。

删除会话时，原始消息、对应情景、压缩摘要、抽取任务和审核候选一并清理。**已批准语义记忆与来源操作账本仍保留**，删除会话不代表已完成所有事实的遗忘；要移除事实需使用记忆删除/撤销 API。来源丢失的候选不能继续批准。

`GET /api/memory-layers` 查看配置与统计，`GET /api/episodes` 管理记录，`GET /api/episodes/{id}` 取得来源指针，`PATCH /api/episodes/{id}` 固定或归档，`POST /api/episodes/maintenance` 预览或执行维护。详细请求见[API 文档](../../docs/API接口文档.md)。这些接口属于个人数据域，使用全局 API Token；共享 `X-Agent-Key` 不授予访问权限。

## 配置与边界

[config.example.toml](../../config.example.toml) 中 `[memory.embedding]` 配置模型；`[memory.retrieval]` 控制 `vector_backend`（`auto/sqlite-vec/json`）与 JSON 扫描上限；`[memory.worker]` 控制租约、重试和退避；`[memory.markdown]` 控制文件投影。`sqlite-vec` 是可选依赖，未启用时使用有限范围的 JSON 向量扫描。离线评测只覆盖小型种子集，不能代表真实长期使用的准确率。

`[memory.layers]` 默认为语义 3000、情景 1800、程序 2400、摘要 2000、目录 1200、SELF 1200 字符，整体帧 12000 字符。每轮 `recall_memory/recall_episodes/search_history/load_skill` 最多 6 次，初始帧和读取返回共享 12000 字符配额；这些都是近似成本控制，不是精确 token。`enabled=false` 关闭新增分层装配与配额，`episodic_enabled=false` 只关闭新情景记录/召回/维护；既有个人事实审核保持可用。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_memory_review.py tests/test_memory_retrieval_scale.py tests/test_core.py
python -m pytest -q tests/test_episodic_memory.py tests/test_layered_context_budget.py tests/test_memory_layers_integration.py
python -m eval.run_seeded --min-recall 0.75
python -m eval.run_memory_layers --min-pass-rate 1
```
