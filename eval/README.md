# 记忆检索、治理与性能评测

[项目首页](../README.md) · [后端测试](../tests/README.md) · [个人记忆模块](../memoria/memory/README.md) · [归档报告](results/README.md)

本目录将个人记忆检索、共享记忆治理和存储层性能分别评估。以下命令在仓库根目录运行，要求已安装 `requirements.txt`；需要虚拟环境时将 `python` 替换为 `.venv/bin/python`。默认本地 Runner 使用临时 SQLite，不写入运行中的个人数据库，也不调用模型；`--embedding`、LongMemEval 的 `--qa/--judge` 是显式的联网分支。

## 文件导航

| 文件 | 职责 |
| --- | --- |
| [memory_eval.py](memory_eval.py) / [memory_cases.json](memory_cases.json) | 对外部提供的个人检索排名独立评分；默认四条小型标签案例 |
| [run_seeded.py](run_seeded.py) / [seeded_memory_cases.json](seeded_memory_cases.json) | 初始化八条个人记忆，通过查询规划与真实引擎运行十二个中文问题 |
| [governance_eval.py](governance_eval.py) / [governance_cases.json](governance_cases.json) | 二十四条治理案例的评分器、答案模板与指标门槛 |
| [run_governance.py](run_governance.py) | 在真实治理层构造案例、生成实际预测并评分 |
| [longmemeval.py](longmemeval.py) / [run_longmemeval.py](run_longmemeval.py) | 公开 LongMemEval 历史适配、来源召回、API 问答与独立判分 |
| [run_memory_layers.py](run_memory_layers.py) | 离线执行真实情景/语义/技能/预算模块，生成十五项分层机制检查 |
| [benchmark_shared_memory.py](benchmark_shared_memory.py) | 共享存储层的容量、授权检索、详情与越权拒绝测量 |
| [test_governance_eval.py](test_governance_eval.py) | 评分器与真实 Runner 的回归用例 |
| [results](results/README.md) | 已记录的实际报告及实验范围，不是模型或公开集数据目录 |

## 个人记忆检索

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

第二条通过 `Settings.load()` 使用选定配置的 embedding 模型，可能调用外部服务。配置优先级同主程序，可由 `MEMORIA_CONFIG` 指定；须检查输出和配置，不能仅凭传了 `--embedding` 就断言实际启用了向量召回。线上改动前应固定数据集作回归，并逐步加入匿名真实失败案例。

检索分数的 Recall、Precision、MRR 对有预期记忆的案例求均值；`wrong_injection_rate` 表示无关/负样本被注入记忆的比例，`forbidden_hit_rate` 在声明禁用 ID 的案例中统计前 K 命中。`gating_accuracy` 同时检查正样本命中和负样本为空。`run_seeded` 的输出包含 `report` 与 `predictions`，独立 `memory_eval` 需要的是其中的预测映射，不是整个 Runner 输出文件。

较大记忆库中的词面召回边界由 `tests/test_memory_retrieval_scale.py` 覆盖：220 条高重要性干扰记录下，低重要性的较早记忆仍能通过问题片段被召回。运行 `python -m pytest -q tests/test_memory_retrieval_scale.py`。该场景不调用模型，适合在调整 FTS 查询或候选上限时做确定性回归。

## LongMemEval 公开数据评测

默认使用 `data/benchmark/longmemeval_s_cleaned_subset5.json`，完整文件可用 `--data` 显式选择。每题的所有用户/助手历史按轮次分块，在独立临时数据库中调用真实 `MemoryEngine`、`PromptAssembler` 和 `ContextBudget`；参考答案与来源标签只用于评分，带 `answer_` 的会话 ID 不送入系统。

```bash
# 无 API 的关键词检索
python -m eval.run_longmemeval
# 调用 main 回答与 fast 评分；不调用 Embedding
python -m eval.run_longmemeval --qa --judge
# 调用 Embedding、main 和 fast；历史向量在 data/ 下缓存
python -m eval.run_longmemeval --embedding --qa --judge \
  --out data/benchmark/results/longmemeval_subset5_hybrid_qa.json
```

报告区分检索来源、最终注入来源和问答正确率，并记录 API usage、完成状态与失败。截断或仅有推理文本的响应不能作为正式答案评分。`--qa` 输出官方格式 `.hypotheses.jsonl`，便于另行执行官方评分。

这是公开样本上的原始历史 RAG 评测；并未覆盖生产抽取、审批、情景和共享权限全链路。内置判分采用独立量规与项目配置模型，不是官方 GPT-4o 判分。5 题结果不能称为完整公开集成绩。运行参数、指标口径、API 调用与本次实测见[中文说明](../docs/LongMemEval评测说明.md)和[报告](results/longmemeval_subset5.json)。

## 分层记忆机制回归

```bash
python -m eval.run_memory_layers --min-pass-rate 1
python -m eval.run_memory_layers --seed 331456 --min-pass-rate 1 \
  --out /tmp/memoria-memory-layers.json
python -m pytest -q tests/test_memory_layers_eval.py
```

Runner 调用真实 `Store/EpisodicMemory/MemoryEngine/SkillCatalog/PromptAssembler/ContextBudget/TurnMemoryBudget`，在临时 SQLite 和临时技能文件上收集 observed，预期值仅参与评分。共 15 项检查：跨会话相关性与无关拒答、来源 ID、TTL/固定保护、dry-run 无副作用、容量归档与语义保留、来源删除、待审不注入、技能指纹重载、超长事实与技能共存、最新请求/帧保护、工具完整配对、整轮读取次数/字符配额。故障或门槛未达返回非零退出码；`--min-pass-rate` 必须在 0–1。

报告包含日期、seed、协议、配置边界、Python/SQLite 版本、各项 observed/expected/pass 和总体通过率；TTL 用固定时钟推进两天，容量/字符用小值强制触发边界。2026-10-09 的[报告](results/memory_layers_regression.json)为 15/15，通过率 1。**这是十五项已知合成机制回归，不是公开集成绩、真实问答质量、模型 token 成本或延迟测试。**生产默认预算和 TTL 与强制触发边界的评估参数不同，具体见报告 `limits`。

长会话摘要完整性、并发 CAS 和摘要失败回退另由 `tests/test_incremental_compaction.py` 验证；这些检查不能证明真实模型摘要逐事实保真。调参方案见[记忆分层 README](../docs/memory-layers/README.md)。

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

也可直接将真实实现作为回归门槛：

```bash
python -m eval.run_governance --min-pass-rate 1 --max-leak-rate 0
python -m pytest -q eval/test_governance_eval.py
```

`--cases` 支持替换案例文件，`-k` 控制前 K 召回；`--predictions-out` 保存供独立评分的系统预测，stdout 是评分报告。门槛未达到时退出码为 1。

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
