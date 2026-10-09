# 已归档的评测报告

[评测入口](../README.md) · [项目首页](../../README.md)

本目录保留具体实验输出，便于核对 README 中的数字。治理与共享性能两份报告记录于 **2026-10-02**，分层机制报告记录于 **2026-10-09**。旧性能数字没有因新增报告而重新测量；前三份报告为本机合成案例；LongMemEval 新报告来自公开样本的 5 题子集，使用自定义模型判分，也不是完整公开集正式成绩。

| 报告 | 参数与结果 | 适用范围 |
| --- | --- | --- |
| [governance_regression.json](governance_regression.json) | 二十四条案例，全部通过；隔离泄漏率 0，并提供逐案例检查 | 真实治理层上的已知合成回归；不测模型回答与 HTTP |
| [shared_memory_1000.json](shared_memory_1000.json) | 1000 条记忆、10 个空间、200 次查询；search p50/p95 为 0.070/0.080 ms，detail 为 0.026/0.031 ms | 单进程本地 Python/SQLite 存储层；不包含模型、HTTP、网络或并发负载 |
| [longmemeval_subset5.json](longmemeval_subset5.json) | 2026-10-09，5 题关键词/混合检索的会话召回均 100%，轮次召回 90%/100%；自动判分 5/5 与 4/5，偏好题存在评分争议 | 实际公开历史 RAG；自定义 judge，非官方完整集成绩；未覆盖审批和共享 ACL |
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

## LongMemEval 子集报告

新报告归档三种运行：无 API 的关键词基线、关键词问答评分、混合检索问答评分；包含数据 SHA256、代码版本、参数、模型、逐题最终回答、来源指标、usage 与失败尝试说明。两种最终 QA 都采用 4096 输出预算，截断或 reasoning-only 输出不能作为答案。没有针对 gold 答案调整检索或提示词。

偏好题的关键词回答拒绝给建议，却被 judge 接受，疑似误判；混合回答的个性化评分也需复核。两种模式的另外四题均明确匹配事实参考要点。初次关键词运行曾评分 4/5；这些波动说明自定义 judge 不能作为稳定准确率证明。

最终混合运行复用了完整历史向量缓存，不能与关键词运行比较冷启动时延；报告另存首次建立索引的 326 个逻辑批次、3190 个文本和输入字符统计。API 统计不是实际计费金额。完整 500 题未执行，数据和向量未提交。运行方式与官方答案导出见 [LongMemEval 说明](../../docs/LongMemEval评测说明.md)。
