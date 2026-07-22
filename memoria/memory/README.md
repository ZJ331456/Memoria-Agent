# Memory 长期记忆引擎

## 职责

`MemoryEngine` 位于 Store 与 Runtime 之间，负责召回排序和写入前去重。Runtime 不需要知道记忆究竟存 SQLite、向量库还是 Markdown。

## 当前召回

当前采用可解释混合评分：从用户输入抽取中文/英文词元，文本命中权重为 10，叠加 1–5 重要度，profile/preference 再加 2。只返回得分大于零的前 N 条，不再像旧版那样无条件注入无关高权重记忆。

## 自动形成

主回复完成后，fast 模型提取最多三条 profile、preference、fact 或 goal。`add_if_new()` 先把文本规范为字符集合并计算 Jaccard 相似度；相似度达到 0.86 时跳过，抑制重复记忆。

## 后续增强

配置已经提供 embedding 模型。下一步可在不改变 Runtime 的情况下加入向量列、top-k 余弦检索、关键词/向量 RRF 融合、矛盾检测、强化次数和 Markdown consolidation。用户删除必须同时清理所有索引，并保留不含原文的撤销审计。

