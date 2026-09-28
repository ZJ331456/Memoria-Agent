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
