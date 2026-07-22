# Memory 长期记忆引擎

## 1. 模块职责

`memory` 位于 Runtime 与 SQLite Store 之间，统一负责记忆写入、去重、向量生成、历史数据回填、检索和排序。聊天自动提取、HTTP API、`recall_memory` 和 `memorize` 工具均使用同一个 `MemoryEngine`，不能绕过该层直接形成记忆。

## 2. 文件说明

| 文件 | 职责 |
|---|---|
| `embedding.py` | 调用 OpenAI-compatible `/embeddings`，处理分批、超时、重试和响应校验 |
| `engine.py` | 文本去重、向量去重、关键词召回、余弦召回、RRF 融合与旧数据回填 |
| `__init__.py` | 暴露 `EmbeddingClient`、`EmbeddingError` 和 `MemoryEngine` |
| `../store.py` | 保存记忆正文和 JSON 向量，执行旧数据库 schema 迁移 |

## 3. 写入流程

```text
候选记忆
  → 去除首尾空白
  → 字符集合 Jaccard 去重（阈值 0.86）
  → 调 embedding 模型
  → 与已有向量做余弦去重（阈值 0.94）
  → 正文与向量原子写入 SQLite
```

embedding 未配置或服务暂时失败时，写入会自动退化为文本去重，不会让主对话失败。手动 API 创建相似记忆返回 HTTP 409；Agent 工具返回 `saved=false`；自动提取则直接跳过。

## 4. 检索流程

引擎有两条独立召回通道：

1. 关键词通道对英文单词和中文二元词组打分，同时考虑精确子串、记忆类型和重要度。
2. 向量通道生成查询向量，与 SQLite 中的记忆向量计算余弦相似度，仅接纳相似度大于 0.15 的候选。
3. 两条通道使用 Reciprocal Rank Fusion 合并，关键词权重为 0.8，向量权重为 1.0；同分时重要度更高的记忆优先。

引擎内部结果带有 `retrieval.score`、两条通道的排名和向量相似度，用于 trace 与调试；公开 `MemoryResponse` 不暴露向量内容。

## 5. 历史数据与降级

启动旧数据库时，Store 会用 `ALTER TABLE` 自动增加 `embedding` 列。检索时最多惰性回填 64 条无向量记忆，写入去重时最多回填 128 条；也可调用 `POST /api/memories/reindex` 主动批量回填。

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

- 当前没有记忆版本、reinforcement 和 supersede，用户的新旧偏好冲突仍需人工编辑或删除。
- 向量模型更换且维度变化时，应清空旧向量或提供强制重建；当前只会跳过维度不匹配的旧向量。
- 自动提取在主回复之后同步执行；记忆量和并发上升后应迁移到可靠后台队列。
- 尚无 LongMemEval/PersonaMem 形式的离线评估集，阈值应在真实数据上持续校准。
