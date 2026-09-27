# 核心优化第九轮：Markdown 记忆真双层 + 模型 Setup 向导

本轮是既定第 1 轮产品能力：把人类可读记忆层做成运行时读写，并支持页面热配置模型。

## 1. Markdown 记忆真双层

目录默认：`data/markdown/`（`[memory.markdown]` 可配）。

| 文件 | 角色 |
| --- | --- |
| `MEMORY.md` | SQLite 有效记忆的投影；写操作后自动重写；不可手工 PUT |
| `SELF.md` | Agent 自我认知；可编辑；注入 Context Frame `Self` 段 |
| `PENDING.md` | consolidation 候选；后台抽取先 append open，成功写入后移到 done |

API：

- `GET /api/markdown`
- `GET/PUT /api/markdown/{SELF|PENDING}`
- `POST /api/markdown/MEMORY/sync`

Dashboard 记忆页增加 Markdown 双层面板。

## 2. 模型热配置 / Setup 向导

- 覆盖文件：`data/models.override.toml`（随 `data/` 忽略，不进 Git）
- 启动时 `Settings.load` 合并覆盖
- `PUT /api/settings/models` 热更新 main/fast/embedding；空 `api_key` 表示保持原值
- `POST /api/settings/models/test` 做连通性探测
- `GET /api/setup/status` 返回脱敏状态与 `setup_needed`
- 前端新增「设置」页；主模型未配置时引导进入 Setup

API 永不回显密钥，只返回 `configured` / `api_key_set`。

## 验收

```bash
python -m pytest -q
cd frontend && npm run build
```
