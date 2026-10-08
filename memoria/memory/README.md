# Memory：个人长期记忆

[返回核心包](../README.md) · [运行时](../runtime/README.md) · [共享治理](../../docs/记忆治理与多Agent共享记忆.md)

本模块服务**个人对话运行时**。共享 Agent 空间另由 [../governance.py](../governance.py) 管理，尚未接入 `MemoryEngine.retrieve` 或个人 `memorize` 工具。

## 文件映射

| 文件 | 职责 |
|---|---|
| [planner.py](planner.py) | `MemoryQueryPlanner` 门控、可选 fast 模型改写查询/类型/数量 |
| [engine.py](engine.py) | `MemoryEngine` 写入去重、替代、纠正、关键词/BM25/可选向量检索、图扩展和时间查询 |
| [embedding.py](embedding.py) | OpenAI-compatible `/embeddings` 请求、分批、超时及有限重试 |
| [worker.py](worker.py) | 会话结束后领取持久任务，提取候选并写入审核队列 |
| [layer.py](layer.py)、[markdown.py](markdown.py) | `MEMORY.md` 投影、`SELF.md` 和历史 `PENDING.md` 文件 |
| [../store.py](../store.py)、[../vector_index.py](../vector_index.py) | SQLite、FTS、来源操作账本、可选 sqlite-vec |

## 自动提取与人工审核

`AgentRuntime` 保存回复后，以用户消息 ID 为 `source_ref` 投递 `memory_jobs`。`MemoryJobWorker` 使用租约与 heartbeat 领取任务，调用 [LLMClient](../llm.py) 提取候选，存入 `memory_reviews`。**任务完成只表示提取完成，候选尚未生效**；未批准内容不参与检索或 prompt。

用户通过 `GET /api/memory-reviews` 查看候选及原始对话，可编辑正文、类型和重要度后调用 `POST /api/memory-reviews/{id}/approve`，或拒绝。批准才调用 `MemoryEngine.remember`；审核状态在 `pending → applying → approved` 间推进，失败或重启时恢复待审。`GET /api/messages/{id}/source` 与带 `anchor_id` 的会话消息接口定位来源。撤销已批准来源可先用 `POST /api/memories/undo` 的 dry-run 预览。详见[审核与来源定位](../../docs/自动记忆审核与来源定位.md)。

## 写入、检索和版本

手动 API 与个人 `memorize` 工具直接调用 `remember`；规范化正文完全相同的记忆会强化。其他候选按文本/向量相似度交给 fast 模型选择创建、强化或替代；没有决策器时，对部分可变类型有规则式矛盾替代。旧版本与原因保存在替代表；用户纠正产生新版本，可查看时间线。部分写入会生成实体/关键词属性与相关链接；`valid_at/invalid_at` 支持时间查询。个人记忆的 `source_ref` 和操作账本用于来源追踪及撤销。

`retrieve` 从 FTS/关键词种子和当前有效记忆取候选，清理问句尾词后计算词面分、BM25 分和可选向量相似度，按 RRF 融合；再小幅扩展相关记忆图并限制各记忆类型的数量。embedding 不可用时可降级为文本检索；`POST /api/memories/reindex` 可补向量。`MemoryQueryPlanner` 会跳过问候和无关短请求，必要时使用 fast 模型改写查询。检索和个人工具仍只面对个人 `memories` 表。

`MEMORY.md` 从有效结构化记忆重建，不是独立写入源；`SELF.md` 可作为 prompt 上下文。`PENDING.md` 是历史 Markdown 层及 Drift 摘要容器，**自动提取审核队列以 SQLite 为准**。

## 配置与边界

[config.example.toml](../../config.example.toml) 中 `[memory.embedding]` 配置模型；`[memory.retrieval]` 控制 `vector_backend`（`auto/sqlite-vec/json`）与 JSON 扫描上限；`[memory.worker]` 控制租约、重试和退避；`[memory.markdown]` 控制文件投影。`sqlite-vec` 是可选依赖，未启用时使用有限范围的 JSON 向量扫描。离线评测只覆盖小型种子集，不能代表真实长期使用的准确率。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_memory_review.py tests/test_memory_retrieval_scale.py tests/test_core.py
python -m eval.run_seeded --min-recall 0.75
```
