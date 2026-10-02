# 记忆检索评测

`memory_cases.json` 是最小基准集，覆盖偏好、目标、替代记忆和无需召回的负样本。预测文件格式为 `{ "case-id": ["memory-id", ...] }`。

```bash
python -m eval.memory_eval predictions.json -k 5
```

输出 Recall@K、Precision@K、MRR、错误注入率、禁用记忆命中率和门控准确率。

仓库还提供 12 条可直接运行的中文种子评测：

```bash
python -m eval.run_seeded --min-recall 0.75
python -m eval.run_seeded --embedding --min-recall 0.85
```

第二条会使用本仓库 `config.toml` 的 embedding 模型。线上改动前应固定数据集作回归，并逐步加入匿名真实失败案例。

较大记忆库中的词面召回边界由 `tests/test_memory_retrieval_scale.py` 覆盖：220 条高重要性干扰记录下，低重要性的较早记忆仍能通过问题片段被召回。运行 `python -m pytest -q tests/test_memory_retrieval_scale.py`。该场景不调用模型，适合在调整 FTS 查询或候选上限时做确定性回归。

## Memory Governance + Multi-Agent Shared Memory 小型评测

`governance_cases.json` 是 **24 条合成回归案例**，包含三个 Agent、两个团队、读写角色与授权撤销、私有与团队记忆、固定的有效期与撤回时间、替代版本和原始消息。它不需要外部模型，也不是公开数据集或真实用户性能结果。

检索问题刻意使用记忆内容中明确出现的关键词，使分数主要反映访问策略、生命周期和来源链路；语义理解能力应另外用上方的检索集或更大真实数据集衡量。

案例分为五类：

| 检查 | 关注点 | 指标 |
| --- | --- | --- |
| 授权召回 | 本人或同团队 Agent 能否找到有效记忆 | `authorized_recall_at_k`、`authorized_precision_at_k` |
| 访问隔离 | 私有、跨团队记忆或来源是否泄漏；撤权后已知 ID 能否直查；越权写入是否被拒绝 | `isolation_leak_rate`，越低越好 |
| 生命周期 | 过期、撤回、替代前后是否返回正确版本 | `lifecycle_accuracy` |
| 写入决策 | 冲突候选是否待审、越权候选是否拒绝 | `decision_accuracy`、`conflict_review_accuracy` |
| 来源追溯 | 记忆是否指向正确消息与会话 | `source_exact_match_rate` |

直接运行本地实现，生成实际预测并评分：

```bash
python -m eval.run_governance --predictions-out /tmp/memoria-governance-actual.json
python -m eval.governance_eval /tmp/memoria-governance-actual.json -k 5
```

Runner 对每条案例创建独立的临时 `Store`，通过 `MemoryGovernance` 创建 Agent、授权、提议并批准种子记忆，然后调用真实的 `search`、`detail` 和消息来源查询生成预测。已知 ID 案例直接调用 `detail`，验证私有和撤权后的来源隔离。过期案例先批准记忆，再把临时数据库中的有效期设为固定过去时间，以模拟给定的 `as_of`；这是测试时钟替代步骤，不是生产写入流程。Runner 不读取案例的预期答案来生成预测。

来源指标校验后端的记忆、消息和会话三元组，不覆盖浏览器中的跳转交互；此评测也不测吞吐量或延迟。

也可生成空白答案文件，接入其他实现后独立评分：

```bash
python -m eval.governance_eval --write-template /tmp/memoria-governance-predictions.json
python -m eval.governance_eval /tmp/memoria-governance-predictions.json -k 5
```

预测文件以案例 ID 为键，例如：

```json
{
  "team-peer-recall": {
    "memory_ids": ["m-alpha-release"],
    "sources": [{
      "memory_id": "m-alpha-release",
      "message_id": "msg-alpha-release",
      "session_id": "session-alpha-release"
    }]
  },
  "contradictory-team-write": {"decision": "needs_review"}
}
```

检索结果的 `memory_ids` 按排名排列；`sources` 要与记忆 ID、原始消息 ID 和会话 ID 组成正确三元组。提案的 `decision` 使用 `needs_review`、`eligible` 或 `reject`；`eligible` 仅表示有资格进入审核流程，绝不表示自动发布。案例中的 `as_of` 是固定 UTC 时间；系统适配器需要按该时间构造状态或注入测试时钟。完整案例定义和预期值见 JSON 文件。**空白答案文件的分数只是评分器校验，不代表系统表现。**

`case_pass_rate` 要求该案例所有适用检查均通过，缺失预测按失败计；隔离泄漏会检查完整返回列表及来源，而召回和精确率只计算前 K 项。可设置 CI 门槛：

```bash
python -m eval.governance_eval /tmp/memoria-governance-predictions.json \
  --min-pass-rate 0.8 --max-leak-rate 0
```

Python 适配器可直接调用 `eval.governance_eval.evaluate_governance(fixture, predictions, k=5)`，将系统输出映射到案例 ID。本地 Runner 的分数只衡量这 24 条已知合成案例；后续应加入匿名真实失败案例并保留未见过的测试集，以免把小样本回归分数误当成泛化能力。

## 共享记忆本地性能基准

`benchmark_shared_memory.py` 使用固定随机种子，在临时 SQLite 数据库中通过真实的 `propose`/`approve` 初始化共享记忆，不调用模型或网络。默认创建 **10 个空间、1000 条记忆**；一个 owner 和每个空间各一名 reader、contributor。初始化耗时 `seed_ms` 与查询阶段 `query_phase_ms` 分开报告；授权关键词 `search` 输出 p50/p95/max，已知 ID `detail` 输出 p50/p95。每轮还用另一空间的 reader 访问明确的空间 ID 和记忆 ID，统计拒绝率。

```bash
python -m eval.benchmark_shared_memory
python -m eval.benchmark_shared_memory --memories 100 --spaces 5 --queries 30 \
  --output /tmp/memoria-shared-benchmark.json
```

可用 `--memories`、`--spaces`、`--queries`、`--seed` 调整规模；限制分别是记忆不超过 5000、空间 2–50、查询 1–5000，记忆数至少等于空间数。p50/p95 使用最近秩法。脚本会确认授权查询确实返回种子记忆，并把 403/404 计作越权拒绝；其他错误直接使运行失败。**这些延迟只反映运行机器上的本地同步 Python/SQLite 路径，不包含 HTTP、模型、网络或并发负载，不能作为线上 SLO。**

`results/governance_regression.json` 保存 2026-10-02 的 24 条实际治理回归报告；`results/shared_memory_1000.json` 保存 1000 条记忆、10 个空间、200 次查询的本机样例（含 Python/SQLite 版本和参数）。该样例的 search p50/p95 为 0.070/0.080 ms，越权检索与详情分别 200/200 被拒绝；重新运行的时间可能因机器与缓存不同而变化。

两份归档报告的 `measurement_date`、`scope` 和 `environment` 是记录时补充的实验元数据；当前命令直接输出指标与参数，归档时需另外记录日期、测试范围及 Python/SQLite/机器版本。
