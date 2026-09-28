<p align="right"><strong>简体中文</strong> · <a href="./README.en.md">English</a></p>

# Memoria Agent

**一个让你能检查并纠正记忆的本地个人 Agent。** 它会在对话中使用长期记忆，也把记忆的来源、变化和纠正过程交还给你。

> 对话 → 提取候选 → 查看原话并审核 → 检查或纠正记忆 → 在后续对话中使用当前版本

Memoria 面向本地单用户使用。会话和记忆保存在本机 SQLite；模型请求发送到你配置的 OpenAI 兼容服务。

## 你能做什么

- **持续对话**：多会话保存、流式回复、停止生成，以及超出上下文窗口时的会话摘要。
- **审核长期记忆**：对话提取的事实和偏好先进入审核队列；查看原始消息、修改候选，再批准或拒绝。批准前不会参与召回。
- **检查并纠正**：在记忆库点击后立即打开独立编辑窗口；从来源 ID 跳回原始对话，查看版本链，填写原因后生成纠正版。旧版本保留，只有当前有效版本参与召回。
- **使用工具**：内置记忆、历史搜索、计算和网页读取工具；Tool Search 按需暴露工具，MCP 可接入外部服务。
- **了解运行过程**：Dashboard 展示记忆任务、工具调用和运行追踪；可选的 Drift 在空闲时按预算执行限定技能。

## 快速开始

需要 **Python 3.11+**、**Node.js/npm**、Git，以及一个 OpenAI 兼容的聊天模型接口。以下命令适用于 Linux/macOS；Windows 可使用 WSL。

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

模型设置保存在被 Git 忽略的 `data/models.override.toml`，API 不回显密钥。也可以复制 `config.example.toml` 为 `config.toml`，用环境变量配置模型和运行选项。

## 第一次使用

1. 在「对话」页告诉 Memoria 一项真实偏好或目标，完成一次对话。
2. 到「记忆 → 待审核候选」查看候选，点击「查看原始对话」核对上下文，修改候选后批准，或直接拒绝。
3. 在「记忆 → 有效记忆库」点击「检查并纠正」，编辑窗口会立即出现并聚焦内容输入框；填写修改原因后保存，新版本生效，旧版本留在时间线中。

「记忆 → 存储与任务」提供 Markdown 视图和后台抽取记录：`MEMORY.md` 从有效记忆生成，`SELF.md` 可由用户编辑并注入上下文。未保存的 Markdown 修改会暂存在当前浏览器标签页，保存文件后清除；旧的 `PENDING.md` 仍可查看，新的审核队列以 SQLite 为准。

## 可选能力

需要修改运行选项时，先执行 `cp config.example.toml config.toml`；修改后重启服务。

| 能力 | 开启方式 |
| --- | --- |
| 语义检索 | 在「设置」中配置 Embedding 模型；需要 SQLite 向量索引时运行 `.venv/bin/python -m pip install -r requirements-vector.txt` |
| MCP 工具 | 运行 `cp mcp_servers.example.json data/mcp_servers.json`，按需配置服务并在页面重载 |
| 空闲 Drift | 在 `config.toml` 的 `[agent.drift]` 下设置 `enabled = true`；默认关闭，可设置空闲阈值、静默时段、日预算和工具白名单 |
| API 访问限制 | 在 `config.toml` 的 `[server.security]` 下配置 `api_token`、允许的 Origin 和限流 |

运行数据默认位于 `data/`，服务默认只监听 `127.0.0.1:2237`。升级依赖后请重启服务。

## 开发与验证

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest -q
npm run build --prefix frontend
(cd frontend && npx playwright install chromium)
npm run test:e2e --prefix frontend
```

前端开发可另开终端运行 `npm run dev --prefix frontend`，Vite 会将 `/api` 代理到本地后端。
