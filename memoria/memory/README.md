# Memory 长期记忆引擎

## 1. 模块职责

`memory` 位于 Runtime 与 SQLite Store 之间，统一负责记忆写入、去重、向量生成、历史数据回填、检索和排序。聊天自动提取、HTTP API、`recall_memory` 和 `memorize` 工具均使用同一个 `MemoryEngine`，不能绕过该层直接形成记忆。

## 2. 文件说明

| 文件 | 职责 |
|---|---|
| `embedding.py` | 调用 OpenAI-compatible `/embeddings`，处理分批、超时、重试和响应校验 |
| `engine.py` | 强化/创建/替代决策、关键词召回、余弦召回、RRF 融合与旧数据回填 |
| `__init__.py` | 暴露 `EmbeddingClient`、`EmbeddingError` 和 `MemoryEngine` |
| `../store.py` | 保存记忆正文和 JSON 向量，执行旧数据库 schema 迁移 |

## 3. 写入流程

```text
候选记忆
  → 去除首尾空白
  → 规范文本完全相同：强化已有记忆
  → 调 embedding 模型
  → 同类型候选预筛（文本/向量相似度至少 0.55）
  → fast 模型输出 create / reinforce / supersede
  → 正文、向量、状态和替代历史原子写入 SQLite
```

`reinforce` 只更新已有条目的 `reinforcement`、`last_reinforced_at` 和 `updated_at`。`supersede` 只允许用于 preference、profile、goal、procedure；新条目指向 `supersedes_id`，旧条目标记为 `superseded`，`memory_replacements` 保存新旧正文快照、原因和时间。模型无效输出、目标越界或服务失败时保守选择 `create`，不会自动退休旧信息。

embedding 未配置或暂时失败时，写入仍可执行规范文本强化及关键词候选判断，不会让主对话失败。手动 API 和 Agent 工具都会返回结构化的 `created/reinforced/superseded` 动作；自动提取只把 created/superseded 计入本轮新增，但已有条目的强化次数仍会增加。

## 4. 检索流程

引擎有两条独立召回通道：

1. 关键词通道对英文单词和中文二元词组打分，同时考虑精确子串、记忆类型和重要度。
2. 向量通道生成查询向量，与 SQLite 中的记忆向量计算余弦相似度，仅接纳相似度大于 0.15 的候选。
3. 两条通道使用 Reciprocal Rank Fusion 合并，关键词权重为 0.8，向量权重为 1.0；强化次数形成有上限的小幅加权，同分时重要度更高的记忆优先。
4. 只有 `active` 记忆参与召回；`superseded` 条目只用于审计，不会注入模型上下文。

引擎内部结果带有 `retrieval.score`、两条通道的排名和向量相似度，用于 trace 与调试；公开 `MemoryResponse` 不暴露向量内容。

## 5. 历史数据与降级

启动旧数据库时，Store 会用 `ALTER TABLE` 自动增加 `embedding`、`status`、`reinforcement`、`supersedes_id` 和 `last_reinforced_at`。检索时最多惰性回填 64 条无向量记忆，写入预筛时最多回填 128 条；也可调用 `POST /api/memories/reindex` 主动批量回填。

以下情况只记录 warning 并回退关键词检索：embedding 未配置、网络超时、供应商 429/5xx、响应数量或维度异常。API Key 只用于请求 header，不写入数据库、trace 或 API 响应。

## 6. 配置

```toml
[memory.embedding]
model = "text-embedding-v3"
api_key = "${DASHSCOPE_API_KEY}"
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

`base_url` 应指向兼容 API 的 `/v1` 根路径，客户端会追加 `/embeddings`。当前向量以 JSON 保存，适合本地中小规模数据；达到数万条后再考虑 sqlite-vec，并保持 `MemoryEngine` 接口不变。

## 7. 已知边界

- 当前替代关系可审计，但尚未提供“一键恢复旧版本”的 API；恢复策略需要同时处理当前替代项，不能简单把两条都激活。
- 向量模型更换且维度变化时，应清空旧向量或提供强制重建；当前只会跳过维度不匹配的旧向量。
- 自动提取在主回复之后同步执行；记忆量和并发上升后应迁移到可靠后台队列。
- 尚无 LongMemEval/PersonaMem 形式的离线评估集，阈值应在真实数据上持续校准。
