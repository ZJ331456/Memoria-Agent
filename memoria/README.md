# Memoria 核心包

[返回项目首页](../README.md) · [系统架构](../docs/系统架构与实现说明.md) · [API 文档](../docs/API接口文档.md)

此目录承载本地个人 Agent 的对话运行时，以及独立的多 Agent 共享记忆治理层。HTTP 入口位于 [api/](api/README.md)；一轮个人对话由 [service.py](service.py) 装配 [runtime/](runtime/README.md)、工具、记忆和模型。共享记忆由 [governance.py](governance.py) 管理空间、授权、提议、审批、版本和审计，走 [api/governance.py](api/governance.py)；**它不会自动进入个人对话的记忆召回或工具写入路径**。已有个人记忆可由管理员发起导入提议，批准前仍不可被共享检索。

## 模块导航

| 模块 | 职责 |
|---|---|
| [api/](api/README.md) | FastAPI、SSE、鉴权、个人记忆接口与共享治理接口 |
| [runtime/](runtime/README.md) | 一轮对话、工具循环、会话压缩与取消 |
| [memory/](memory/README.md) | 个人长期记忆检索、写入、自动提取与人工审核 |
| [lifecycle/](lifecycle/README.md) | 对话五阶段异步扩展点 |
| [prompting/](prompting/README.md) | 上下文帧组装和字符预算 |
| [tools/](tools/README.md) | 工具注册、校验、执行、授权和按需发现 |
| [mcp/](mcp/README.md) | 外部 stdio MCP server 接入工具注册表 |
| [skills/](skills/README.md) | 仅扫描技能目录中的 `SKILL.md` 并选择注入 |
| [drift/](drift/README.md) | 空闲时的限额后台 Agent 轮次 |
| [observability/](observability/README.md) | 运行追踪、事件总线、请求上下文和指标 |

## 根级文件

| 文件 | 职责 |
|---|---|
| [config.py](config.py) | 加载 [config.example.toml](../config.example.toml) 与环境变量占位符 |
| [llm.py](llm.py)、[models_config.py](models_config.py) | OpenAI-compatible 模型调用、模型配置展示与更新 |
| [service.py](service.py) | 组装个人运行时、worker、Drift、Skills 与 MCP |
| [store.py](store.py)、[vector_index.py](vector_index.py) | SQLite 数据、FTS、任务租约及可选 sqlite-vec 索引 |
| [governance.py](governance.py) | 与个人 `memories` 表分离的 Agent 空间、权限和审核记录 |
| [security.py](security.py) | 可选服务级 Token、Origin、请求大小和速率限制 |

个人对话的主路径是 `api → AgentService → AgentRuntime → MemoryEngine / ToolRegistry → Store / LLMClient`。共享治理的主路径是 `api/governance.py → MemoryGovernance → Store.db`，由 Agent key 识别主体，再按空间角色检查每次读写。两条路径共享 SQLite 连接，但使用不同表和访问规则；更多边界见[记忆治理与多 Agent 共享记忆](../docs/记忆治理与多Agent共享记忆.md)。

## 从仓库根目录验证

```bash
python -m pytest -q tests/test_core.py tests/test_governance_core.py tests/test_governance_api.py
python -m eval.run_seeded
```

以上单元测试和种子评测可在不配置在线模型的情况下运行；在线聊天仍需要有效模型配置。
