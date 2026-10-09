# 公开数据集评测

[项目首页](../README.md) · [实际报告](results/README.md) · [功能覆盖与协议](../docs/公开数据集评测方案.md) · [工程测试](../tests/README.md)

`eval/` 只保留公开 benchmark 的下载、适配与量化结果。自定义问题、合成负载、机制回归及其评分器已经迁至 [tests/regression](../tests/regression/README.md)，不再作为公共数据集成绩展示。

## 数据集与五题试跑

| 数据集 | 固定样本 | 主要能力 | API |
| --- | --- | --- | --- |
| [LongMemEval](https://github.com/xiaowu0162/LongMemEval) | 用户已有 5 题子集 | 长历史检索、事实、偏好、时间与更新 | 默认离线；Embedding、reader、judge 可选 |
| [LoCoMo](https://github.com/snap-research/locomo) | conv-26 中五类各取原始顺序首题，共 5 题 | 多会话证据、人物归属、时间、推断与错误前提拒答；语义/情景分层注入 | 默认离线；Embedding、reader、judge 可选 |
| [GateMem](https://github.com/rzhub/GateMem) | 4 个领域共 5 个公开检查点：效用 1、访问控制 2、主动遗忘 2 | 多主体共享、权限边界、删除后回答、来源及共享路径时延 | fast 解析权限、main 回答、fast 评分；不调用 Embedding |

GateMem 是上游发布的合成 benchmark，包含公开且独立的评分标签，不是本项目自行创建的场景，也不是生产用户记录。所有三组结果只是小样本适配试跑，不是官方完整榜单成绩；公开 QA 集也不能覆盖所有工程边界。

## 下载与运行

在仓库根目录安装 `requirements.txt`。数据统一放在 Git 忽略的 `data/benchmark/`，数据集原文与向量均不提交。

```bash
# 固定上游 Git 版本与 SHA256，下载原始公开数据并抽选五条；不会调用模型
python -m eval.prepare_public

# 已有 LongMemEval 五题（不重新跑完整 500 题）
python -m eval.run_longmemeval
python -m eval.run_longmemeval --embedding --qa --judge

# LoCoMo 默认只运行准备好的五题
python -m eval.run_locomo
python -m eval.run_locomo --embedding --qa --judge

# GateMem 只运行五个检查点，调用配置中的 LLM API
python -m eval.run_gatemem
```

LoCoMo/GateMem 入口拒绝超过五条的输入；目前没有自动扫全量数据的批量入口。LongMemEval 仍支持显式 `--data`，默认固定用户五题文件，本次没有运行其完整集。LoCoMo/GateMem 上游版本、原始文件校验和、原始样本 ID 固定在 [benchmarks.json](benchmarks.json)，`prepare_public` 严格校验下载或已有缓存；校验失败须检查文件，不能自动忽略。

默认报告输出在 `data/benchmark/results/`。只有完成核对的量化结果才归档至 `eval/results/`。LoCoMo 使用 1600 字符分块、语义 top-10、情景 top-3、12000 字符帧；GateMem 编译最多 40 条带权限的状态记忆，枚举真实 ACL 过滤结果后按问题词排序、取前 20 条。reader 输出预算 4096，GateMem compiler 8192。它们均使用实际 `ContextBudget` 的最终限制；字符不是精确 token。

## 适配边界

- **LongMemEval**：全部用户与助手历史建原始索引；调用实际 `MemoryEngine` 和上下文模块，不执行生产抽取审批。详见[专门说明](../docs/LongMemEval评测说明.md)。
- **LoCoMo**：原始 speaker、时间、文本、图片文字描述入索引；`answer/evidence/category/adversarial_answer`、观察、事件摘要与会话摘要均不用于检索或生成。原始轮次同时导入真实 `EpisodicMemory`；`completed` 表示导入完成，不是任务成功。语义单层只比较来源召回，最终 QA 使用语义加情景上下文。
- **GateMem**：只读取 `as_of_turn_id` 前缀。自然语言权限由 LLM 转为权限状态快照，再实际调用 `MemoryGovernance.propose/approve/grant/revoke_memory/search/detail`。系统输入不含 `query_type/attack_type/expected_action/judge_spec/leak_targets`。审批是评测导入操作，不能证明人工审核质量；权限编译器是评测适配器，不能据此声称生产聊天已有自动权限理解。

所有 reader/judge 必须提供正式 `content`；截断或 reasoning-only 输出记为失败。参考答案只送入评分阶段。GateMem 另用公开 `leak_targets/not_include` 对最终答案作正则泄漏下界检查；这些目标不参与权限解析、检索、回答或历史筛选。内置模型判分可能出错，报告保留逐题回答、理由和失败分母。

## 报告口径

公开集指标包括证据召回、最终注入来源、QA 自动判分、效用/访问/遗忘检查点通过率与来源 API 对应率。失败案例按零计入请求分母；无有效评分时不能报告零泄漏。来源 ID 命中不保证答案正文完整进入帧，结构性来源对应也不能证明 LLM 归纳正确。

GateMem 的共享 search、detail/source 和状态导入时延来自这五个公开前缀的实际数据库调用；没有合成 1000 条记忆。LoCoMo 语义检索计时包含问题 Embedding 网络调用，若启用，不能称为纯 SQLite 时延。五个请求不足以估计负载下 p95/SLO；报告只提供样本数、median/min/max。

TTL、置顶、并发审批、CAS、工具完整配对、技能指纹、草稿保护和浏览器点击等由 [tests](../tests/README.md)验证。公开问答集没有这些验收标签，报告明确标为未评测，不编造基准答案补齐。

## 文件导航

| 文件 | 职责 |
| --- | --- |
| [benchmarks.json](benchmarks.json) / [prepare_public.py](prepare_public.py) | 上游版本、原始 SHA256、固定 ID、下载与无改写抽样 |
| [longmemeval.py](longmemeval.py) / [run_longmemeval.py](run_longmemeval.py) | 已有 LongMemEval 输入隔离、检索、问答与评分 |
| [run_locomo.py](run_locomo.py) | LoCoMo 原始证据、真实语义/情景模块与最终上下文问答 |
| [run_gatemem.py](run_gatemem.py) | 公开治理前缀解析、真实共享 API、访问与遗忘评分 |
| [public_common.py](public_common.py) | 元数据、严格 JSON、API usage 和小样本时延 |
| [results](results/README.md) | 公开五题试跑的实际输出及局限 |

旧入口迁移清单见 [regression README](../tests/regression/README.md)。本次删除了 `governance_regression.json`、`shared_memory_1000.json` 和 `memory_layers_regression.json`，它们没有被改名冒充公开成绩。
