# Memoria Agent

一个可运行的“对话 + 长期记忆 + 可观测 Dashboard”个人 Agent MVP。模型默认复用 `../akashic-agent/config.toml`，API 不会向前端返回密钥。

## 启动

```bash
cd /root/project_job/Memoria-Agent
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
.venv/bin/python main.py
```

访问 `http://127.0.0.1:2237`。开发前端时可另开终端运行 `cd frontend && npm run dev`，Vite 会把 `/api` 代理到 2237。

自定义配置：复制 `config.example.toml` 为 `config.toml`，或通过 `python main.py --config /path/to/config.toml` 指定。数据库默认位于 `data/memoria.db`。

## 当前核心闭环

- 多会话对话及 SQLite 持久化
- OpenAI-compatible 主模型调用
- 多步模型工具调用循环与安全迭代上限
- 五阶段生命周期流水线
- 相关长期记忆注入
- 混合排序记忆召回、快速模型自动提取与相似去重
- 长期记忆搜索、手动新增和删除
- 内置记忆、历史、时间和安全计算工具
- 会话、消息、记忆、模型、工具和生命周期状态面板
- Turn trace：耗时、召回记忆、工具链与错误记录
- API Key 脱敏（只返回是否已配置）

详细架构、模块边界、接口和后续阶段见 [系统架构与实现说明.md](docs/系统架构与实现说明.md)。

API 使用、错误协议、请求示例和全部端点见 [API接口文档.md](docs/API接口文档.md)。服务启动后也可以直接访问 `http://127.0.0.1:2237/docs` 使用 Swagger UI。
