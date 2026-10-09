# 已归档的评测报告

[评测入口](../README.md) · [项目首页](../../README.md)

本目录保留具体实验输出，便于核对 README 中的数字。治理与共享性能两份报告记录于 **2026-10-02**，分层机制报告记录于 **2026-10-09**。旧性能数字没有因新增报告而重新测量；各报告均为本机合成案例，不是公开评测集正式成绩。

| 报告 | 参数与结果 | 适用范围 |
| --- | --- | --- |
| [governance_regression.json](governance_regression.json) | 二十四条案例，全部通过；隔离泄漏率 0，并提供逐案例检查 | 真实治理层上的已知合成回归；不测模型回答与 HTTP |
| [shared_memory_1000.json](shared_memory_1000.json) | 1000 条记忆、10 个空间、200 次查询；search p50/p95 为 0.070/0.080 ms，detail 为 0.026/0.031 ms | 单进程本地 Python/SQLite 存储层；不包含模型、HTTP、网络或并发负载 |
| [memory_layers_regression.json](memory_layers_regression.json) | seed 331456，真实本地模块执行 15/15 分层机制检查通过；含逐项 observed/expected | 2026-10-09 离线机制回归，不测真实回答、摘要保真、token 成本或延迟 |

性能报告中初始化种子耗时 `seed_ms` 单独记录，授权查询计时与越权拒绝数量分开；每空间约 100 条合成记忆，数据已初始化。跨空间检索与详情均 200/200 被拒绝。此样例不能用作线上 SLO 或吞吐量承诺。

## 重新生成指标

在仓库根目录运行，先输出至临时文件，再审阅并决定是否作为新报告保存：

```bash
python -m eval.run_governance --min-pass-rate 1 --max-leak-rate 0 \
  --predictions-out /tmp/memoria-governance-predictions.json \
  > /tmp/memoria-governance-report.json

python -m eval.benchmark_shared_memory --memories 1000 --spaces 10 --queries 200 \
  --seed 331456 --output /tmp/memoria-shared-benchmark.json

python -m eval.run_memory_layers --seed 331456 --min-pass-rate 1 \
  --out /tmp/memoria-memory-layers.json
```

旧治理/性能归档的 `measurement_date`、`scope` 和 `environment` 是记录时补充的元数据；对应两个 CLI 不自动生成这些字段。新增分层 Runner 会生成日期、环境、协议与参数，归档时还应记录对应 Git 提交。重新运行的时间可能变化，固定随机种子也不意味着延迟完全相同。

不要把真实用户正文、模型凭据、Agent key 或未脱敏日志放入报告。本目录只保留小型 JSON 报告，原始公开数据和模型权重应按各自许可证存放在仓库外。
