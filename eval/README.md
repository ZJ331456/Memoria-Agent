# 记忆检索评测

`memory_cases.json` 是最小基准集，覆盖偏好、目标、替代记忆和无需召回的负样本。预测文件格式为 `{ "case-id": ["memory-id", ...] }`。

```bash
python -m eval.memory_eval predictions.json -k 5
```

输出 `recall_at_k`、`mean_reciprocal_rank` 和 `wrong_injection_rate`。线上改动前应保留同一数据集作回归；真实部署建议将 ID 替换为隔离测试库内的固定种子记忆。
