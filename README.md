<p align="right"><strong>简体中文</strong> · <a href="./README.en.md">English</a></p>

# Memoria Agent

**一个可审核、可追溯、可授权给多个 Agent 使用的本地记忆系统。** 它让人决定哪些事实能成为共享记忆、谁能读取，以及何时纠正或撤销。个人 Agent 工作台仍可用来产生和检查记忆。

> Agent 提案 → 人工审核 → 按空间授权共享 → 冲突版本确认 → 撤销与审计

Memoria 默认本地运行，数据保存在 SQLite；只有聊天和自动提取等模型能力会请求你配置的 OpenAI 兼容服务。共享记忆治理 API 不依赖模型即可运行。

## 核心流程

```mermaid
flowchart LR
    A[Agent 提交事实与来源] --> B[空间内待审提案]
    B --> C[Owner / Curator 审核]
    C --> D[有效共享记忆]
    D --> E[有权限的 Agent 检索]
    D --> F[纠正 / 过期 / 撤销]
    F --> G[版本链与审计事件]
```

共享层在检索与已知 ID 查询前检查空间权限；同主题变更必须确认当前版本，避免并发审批覆盖。个人对话产生的记忆使用独立审核队列，管理员可将有效个人记忆显式导入为共享提案。

## 你能做什么

- **治理共享记忆**：为 Agent 创建独立密钥和私有/共享空间；按 reader、contributor、curator 授权；提案须审核，批准前不会进入共享检索。
- **控制版本与生命周期**：同主题冲突需要显式确认被替代版本；过期、撤销与授权变更会立即影响共享检索，保留可审计的来源和事件。
- **审核长期记忆**：对话提取的事实和偏好先进入审核队列；查看原始消息、修改候选，再批准或拒绝。批准前不会参与召回。
- **检查并纠正**：在记忆库点击后立即打开独立编辑窗口；从来源 ID 跳回原始对话，查看版本链，填写原因后生成纠正版。旧版本保留，只有当前有效版本参与召回。
- **分层使用记忆**：工作上下文按层分配预算；任务情景可检索并回查来源；事实经过审核，技能有版本指纹；过期经历软归档，重要经历可固定保留。
- **持续对话与工具**：多会话、流式回复、记忆召回、工具循环与 MCP；Dashboard 展示任务和 trace，Drift 可在空闲时运行限定技能。

## 记忆架构

| 职责 | 实现 |
| --- | --- |
| 工作记忆 | 最近消息与增量摘要，保护最新请求，按分区裁剪上下文 |
| 情景记忆 | 跨会话任务/执行结果、来源消息与 trace；相关性召回 |
| 语义记忆 | 经确认的事实、偏好与目标；来源、纠正和版本治理 |
| 程序性记忆 | 按需加载 `SKILL.md`，记录内容版本指纹 |
| 遗忘与治理 | TTL、软归档、固定保留、版本替代及共享权限/撤销 |

遗忘贯穿各层；任务结束不会自动升级成事实或技能。默认情景保留 90 天、召回 top 3，整体记忆帧上限 12000 字符，可在 `[memory.layers]` 调整。完整中文分层解释与调研入口见[记忆模块 README](memoria/memory/README.md)。这些配置是本地调优起点，字符预算不等于精确 token 预算。

个人记忆的创建、替代、纠正和来源撤销统一使用事务维护有效区间；撤销恢复会保留中间版本的历史。指定时间的查询也使用混合检索，图扩展按边权累计后重排。Embedding 按模型和维度隔离，切换不会清空旧向量；共享提案在提交和批准时检查本地来源是否存在。本地来源存在不代表提案内容属实，仍需审核者核对。

## 快速开始

需要 **Python 3.11+**、**Node.js/npm** 和 Git；使用聊天或自动提取时还需要一个 OpenAI 兼容的模型接口。以下命令适用于 Linux/macOS；Windows 可使用 WSL。

```bash
git clone https://github.com/ZJ331456/Memoria-Agent.git
cd Memoria-Agent

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefix frontend
npm run build --prefix frontend
.venv/bin/python main.py
```

打开 <http://127.0.0.1:2237>。首次进入会提示配置主模型的 **Model、Base URL 和 API Key**；可以在页面中测试连接。快速模型和 Embedding 模型可稍后配置。未配置 Embedding 时仍可使用词面记忆检索。

Windows PowerShell 可直接使用 `.\.venv\Scripts\python.exe main.py` 启动。中文本地检索支持 BGE small zh v1.5；项目内模型目录、独立环境与 CUDA/CPU 配置见[本地 Embedding 配置](docs/本地Embedding配置.md)。

想先体验共享记忆治理，可直接打开「共享治理」页；此流程不要求先配置模型。Agent 密钥只在页面内存中使用，刷新后需重新粘贴。

模型设置保存在被 Git 忽略的 `data/models.override.toml`，API 不回显密钥。也可以复制 `config.example.toml` 为 `config.toml`，用环境变量配置模型和运行选项。

## 第一次使用

### 共享记忆协作

1. 在「共享治理」创建 Agent，保存只显示一次的密钥；连接该身份后创建空间，创建者成为 owner。
2. 创建另一个 Agent，将其 ID 授权为 reader、contributor 或 curator。reader 检索，contributor 提交，curator 审核；owner 管理成员权限。
3. 提交内容、稳定的 `topic_key` 和来源。owner / curator 填写原因后批准或拒绝；涉及已有主题的变更需确认被替代版本。
4. 用其他身份检索已批准内容并查看版本链；owner / curator 可查看撤销后的审计记录。私有空间只允许 owner 使用。

### 个人记忆工作台

1. 在「对话」页告诉 Memoria 一项真实偏好或目标，完成一次对话。
2. 到「记忆 → 待审核候选」查看候选，点击「查看原始对话」核对上下文，修改候选后批准，或直接拒绝。
3. 在「记忆 → 有效记忆库」点击「检查并纠正」，编辑窗口会立即出现并聚焦内容输入框；填写修改原因后保存，新版本生效，旧版本留在时间线中。

「记忆 → 存储与任务」提供 Markdown 视图和后台抽取记录：`MEMORY.md` 从有效记忆生成，`SELF.md` 可由用户编辑并注入上下文。未保存的 Markdown 修改会暂存在当前浏览器标签页，保存文件后清除；旧的 `PENDING.md` 仍可查看，新的审核队列以 SQLite 为准。

旧聊天记忆与新共享空间分开存放；只有管理员显式导入并完成共享提案审核后，旧记忆才会出现在共享检索。当前多 Agent 隔离针对共享记忆接口，旧聊天运行时仍面向本地单用户。

## 可选能力

需要修改运行选项时，先执行 `cp config.example.toml config.toml`；修改后重启服务。

| 能力 | 开启方式 |
| --- | --- |
| 语义检索 | 在「设置」中配置 Embedding 模型；需要 SQLite 向量索引时运行 `.venv/bin/python -m pip install -r requirements-vector.txt` |
| MCP 工具 | 运行 `cp mcp_servers.example.json data/mcp_servers.json`，按需配置服务并在页面重载 |
| 空闲 Drift | 在 `config.toml` 的 `[agent.drift]` 下设置 `enabled = true`；默认关闭，可设置空闲阈值、静默时段、日预算和工具白名单 |
| API 访问限制 | 在 `config.toml` 的 `[server.security]` 下配置 `api_token`、允许的 Origin 和限流 |

运行数据默认位于 `data/`，服务默认只监听 `127.0.0.1:2237`。升级依赖后请重启服务。

## 代码导航

各目录 README 说明真实文件职责、调用关系、配置和验证入口：

| 目录 | 内容 |
| --- | --- |
| [memoria](memoria/README.md) | Python 服务、共享治理与个人 Agent 运行时；继续查看 lifecycle、prompting、memory、tools、MCP 等模块 |
| [frontend](frontend/README.md) | React 工作台、页面与 API、UI 组件、草稿和身份缓存规则 |
| [eval](eval/README.md) | 公共数据集接入、固定五题量化、来源与治理指标 |
| [skills](skills/README.md) | 五个内置技能的使用说明、触发条件及 SKILL.md 编写约定 |
| [tests](tests/README.md) | 按能力选择回归测试、可选依赖与测试范围 |

## 开发与验证

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m tests.regression.run_governance
npm run build --prefix frontend
(cd frontend && npx playwright install chromium)
npm run test:e2e --prefix frontend
```

以上命令在仓库根目录运行。前端开发可另开终端运行 `npm run dev --prefix frontend`，Vite 会将 `/api` 代理到 `http://127.0.0.1:2237`。更多针对模块的命令见各目录 README。

## 公开数据集评测

`eval/` 使用公开 benchmark 的固定五题子集；自定义夹具与机制回归已迁至 `tests/regression/`，原三份合成分数报告已移除。

| 数据集 | 本次范围 | 主要能力 |
| --- | --- | --- |
| LongMemEval | 用户提供的 5 题 | 长历史事实、偏好、时间与知识更新 |
| LoCoMo | 原始五类各 1 题 | 多会话来源、人物归属、拒答与语义/情景分层注入 |
| GateMem | 4 领域共 5 个检查点 | 共享权限、删除后回答、来源和真实共享路径时延 |

```bash
.venv/bin/python -m eval.prepare_public
.venv/bin/python -m eval.run_locomo --embedding --qa --judge
.venv/bin/python -m eval.run_gatemem
# 已有 LongMemEval 五题：默认离线检索，可加 --embedding --qa --judge
.venv/bin/python -m eval.run_longmemeval
```

原始数据位于 `data/benchmark/`，版本、校验和与样本 ID 固定；本次没有运行完整集。GateMem 也是上游合成的公开 benchmark，并非生产记录。LoCoMo 五题自动判分 4/5，标注来源召回 50%；情景层在这五题没有提高来源召回。LongMemEval 的偏好判分存在争议，保留逐题说明。GateMem 访问/遗忘各通过 2/2，但有权效用 0/1，存在过度拒绝。实际报告与适配局限见[评测入口](eval/README.md)和[报告导航](eval/results/README.md)。

这些报告不覆盖全部工程机制，也不是官方完整榜单分数。TTL、并发、技能指纹与 UI 交互由 `tests/` 验证。GateMem 调用实际共享治理 API，仍不能证明旧聊天 runtime 已统一多主体身份。

## Related Projects

以下开源项目为 Memoria-Agent 的设计提供了参考；Memoria-Agent 是独立实现。

- [Pask](https://github.com/xzf-thu/Pask) — 主动式 Agent 与分层长期记忆。
- [akashic-agent](https://github.com/kachofugetsu09/akashic-agent) — Agent 运行时、记忆层与后台任务。
- [claude-mem](https://github.com/thedotmack/claude-mem) — 会话记录、压缩与跨会话上下文召回。
- [Holt](https://github.com/holt-os/holt) — 可查看来源的个人 Agent 记忆。
- [Mem0](https://github.com/mem0ai/mem0) — 面向 Agent 的记忆存储与检索。
- [Graphiti](https://github.com/getzep/graphiti) — 时序知识图谱框架，事实带有效期窗口与混合检索。
- [A-mem](https://github.com/WujiangXu/AgenticMemory) — Agentic Memory，自组织记忆笔记、关联与持续演化。
