# Memoria Agent

一个可运行的“对话 + 长期记忆 + 可观测 Dashboard”个人 Agent。模型凭据保存在被 Git 忽略的 `config.toml`，API 不会向前端返回密钥。

## 启动

```bash
cd /root/project_job/Memoria-Agent
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python main.py
```

访问 `http://127.0.0.1:2237`。开发前端时可另开终端运行 `cd frontend && npm run dev`，Vite 会把 `/api` 代理到 2237。

要启用 SQLite 原生 KNN 向量索引，使用 `.venv/bin/pip install -r requirements-vector.txt`。未安装 `sqlite-vec` 时系统自动回退 JSON 向量扫描。

自定义配置：复制 `config.example.toml` 为 `config.toml`，或通过 `python main.py --config /path/to/config.toml` 指定。数据库默认位于 `data/memoria.db`。

## 当前核心闭环

- 多会话对话及 SQLite 持久化
- OpenAI-compatible 主模型调用
- 多步模型工具调用循环与安全迭代上限
- 五阶段生命周期流水线
- 相关长期记忆注入
- SSE 流式回复、停止生成、断连取消和 cancelled trace
- 关键词/向量双路召回、RRF 融合、自动向量回填与语义去重
- 检索规划/门控、FTS5 候选和按类型限额注入
- 记忆强化、状态版本、LLM 一致性决策和 supersede 替代历史
- 持久化后台记忆任务、租约/续租、可配置重试、按消息来源撤销与旧版本恢复
- 长期记忆搜索、手动新增和删除
- 内置记忆、历史、时间和安全计算工具
- 会话、消息、记忆、模型、工具和生命周期状态面板
- Turn trace：耗时、召回记忆、工具链与错误记录
- 工具写权限、pre-hook、参数白名单、超时和输出上限
- 可选 API Token、Origin 校验、请求限流、请求体上限与 Prometheus `/metrics`
- API Key 脱敏（只返回是否已配置）

详细架构、模块边界、接口和后续阶段见 [系统架构与实现说明.md](docs/系统架构与实现说明.md)。

API 使用、错误协议、请求示例和全部端点见 [API接口文档.md](docs/API接口文档.md)。服务启动后也可以直接访问 `http://127.0.0.1:2237/docs` 使用 Swagger UI。

本轮九项核心优化的实现与验收说明见 [核心优化第五轮：九项落地说明](docs/核心优化审计-第五轮-九项落地.md)。

后续九项生产化增强见 [核心优化第六轮：九项生产化增强](docs/核心优化审计-第六轮-生产化九项.md)。浏览器回归可运行 `cd frontend && npm run test:e2e`。
