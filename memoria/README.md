# Memoria 核心包

`memoria` 是与外部聊天通道无关的 Agent Runtime。FastAPI 只是适配入口，核心执行集中在以下子模块：

| 子模块 | 说明 | 文档 |
|---|---|---|
| `runtime` | 对话与工具循环总编排 | [runtime/README.md](runtime/README.md) |
| `lifecycle` | 五阶段扩展流水线 | [lifecycle/README.md](lifecycle/README.md) |
| `tools` | 工具注册、校验、执行与内置工具 | [tools/README.md](tools/README.md) |
| `memory` | 召回、排序和语义去重 | [memory/README.md](memory/README.md) |
| `observability` | Turn trace 与诊断数据 | [observability/README.md](observability/README.md) |
| `prompting` | 上下文预算与工具消息协议完整性 | [prompting/README.md](prompting/README.md) |

根级文件职责：`config.py` 管理配置，`store.py` 管理 SQLite，`vector_index.py` 提供可选 sqlite-vec KNN，`llm.py` 适配 OpenAI-compatible API，`security.py` 实现可选请求安全边界，`service.py` 保持应用服务入口，`api.py` 提供 HTTP 与静态站点。

`api.py` 集中定义公开 API 的严格请求/响应契约、异常协议、中间件、路由和 SPA 托管，避免 API 定义跨文件跳转。代码级说明见 [`API_README.md`](API_README.md)，完整接口说明位于 [`../docs/API接口文档.md`](../docs/API接口文档.md)，运行时 OpenAPI 位于 `/docs` 与 `/openapi.json`。

依赖方向必须保持：`api → service/runtime → lifecycle + tools + memory + observability → store/llm/config`。底层模块不得反向 import API。
