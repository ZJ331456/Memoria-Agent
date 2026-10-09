# 公开数据集实际报告

[评测入口](../README.md) · [功能覆盖](../../docs/公开数据集评测方案.md) · [项目首页](../../README.md)

这里只归档公开 benchmark 固定样本上的系统实际输出。原先三份自定义报告已删除；机制回归与合成微基准迁至 `tests/regression/`，不再用于展示公共任务成绩。原始数据位于 Git 忽略的 `data/benchmark/`。

## 2026-10-09 五题试跑

| 报告 | 原始样本与结果 | 范围 |
| --- | --- | --- |
| [longmemeval_subset5.json](longmemeval_subset5.json) | 用户五题；关键词/混合检索的标注轮次召回 90%/100%；自动判分 5/5 与 4/5，偏好题存在争议 | 原始历史 RAG，结果沿用前次实测；不是抽取审批全链路 |
| [locomo_subset5.json](locomo_subset5.json) | conv-26 的原 QA 下标 3/0/2/82/152，五类各一题；自动判分 4/5；四道非对抗题的语义/联合证据召回均 50%；来源 API 对应 100% | 实际 MemoryEngine、EpisodicMemory、PromptAssembler/ContextBudget；未证明情景召回增益 |
| [gatemem_subset5.json](gatemem_subset5.json) | 效用 0/1、访问 2/2、遗忘 2/2；动作匹配 4/5；71 条编译状态记忆，11 条执行撤销；pending 可见数 0；来源 API 对应 100% | 4 领域五个公开检查点的权限快照 + 实际共享治理 API；自定义 judge，非官方 MGS |

**失败也保留：**LoCoMo 的“Caroline 研究什么”漏掉 D2:8，错误拒答；情景检索没有提高标注来源召回。GateMem 的合法预算/折扣读取被过度拒绝，只回答了正确日期，因此效用题失败。不能只报告访问/删除通过，忽略系统没有帮助合法用户。

所有评分均为配置模型的自动判分，未运行官方全量 evaluator；小样本比例不代表总体准确率。GateMem 是上游合成公开 benchmark，独立于本项目自定义夹具，但不是真实机构生产记录。结构性来源对应率不证明归纳内容语义准确；无字面泄漏也不能证明所有攻击都安全。

## 共享与分层路径的计时

GateMem 的同五个原始前缀实际生成权限状态记忆，然后测量真实共享读路径：

| 路径 | 样本数 | median / min / max |
| --- | --- | --- |
| shared search + 本地排序 | 5 | 0.454 / 0.351 / 0.658 ms |
| detail + message_source | 16 | 0.079 / 0.047 / 0.158 ms |
| 状态导入 propose/grant/approve/revoke | 5 | 995.544 / 686.812 / 1459.862 ms |

这些耗时不含政策编译、回答与评分 LLM，不能作为负载 p95、线上 SLO 或并发吞吐量。没有生成 1000 条新内容。LoCoMo 语义检索 median 687.008 ms 包含问题 Embedding 网络请求；情景检索 median 18.879 ms 为本地路径，两者不适合直接做纯存储比较。完整逐次计时见 JSON。

## 数据、调用与失败记录

报告包含上游固定 Git 版本、原始文件 SHA256、子集 SHA256、原始样本 ID、模型/端点、参数、逐题最终回答、评分理由、来源、usage 与请求分母。LoCoMo 使用 5 次 reader + 5 次 judge，历史向量可缓存复用；GateMem 最终使用 5 次 fast compiler + 5 次 main reader + 5 次 fast judge。

初次 GateMem main 编译器的 5 个响应均在 8192 预算内未完成，作为失败尝试单独记录；之后 fast 编译器运行相同固定检查点。截断或仅含推理文本的回复不评分。LoCoMo 初始离线接线错误也在报告说明，修复后才执行完整 API 问答，没有因 gold 漏答调整样本或检索。

LongMemEval 沿用前次正式回答检查后的报告，偏好 judge 的疑似误判及历史尝试说明仍保留。模型别名与供应商采样可能导致复跑变化；归档保证原始观察可检查，并不保证相同输入必然产生相同答案。

## 复现

```bash
python -m eval.prepare_public
python -m eval.run_locomo --embedding --qa --judge \
  --out data/benchmark/results/locomo_subset5.json
python -m eval.run_gatemem --out data/benchmark/results/gatemem_subset5.json
```

默认每个新增 runner 仅接收一至五条；完整 LoCoMo/GateMem 未运行，LongMemEval 完整 500 题也未执行。不要将原始历史、密钥或向量加入本目录。公共数据源与许可、工程机制未覆盖项见[方案说明](../../docs/公开数据集评测方案.md)。
