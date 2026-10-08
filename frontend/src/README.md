# 前端源码

[返回前端总览](../README.md) · [页面组件](components/README.md) · [UI 基础组件](components/ui/README.md) · [工具函数](lib/README.md) · [E2E 测试](../e2e/README.md)

## 入口和职责

| 文件 | 职责 |
| --- | --- |
| [`main.tsx`](main.tsx) | React 入口与应用外壳；六个主导航页面的切换、个人会话/记忆/追踪/工具/设置状态。个人记忆页含有效记忆、待审核候选、存储与任务三个视图。 |
| [`api.ts`](api.ts) | 个人工作区的 API 类型与请求封装，包括 SSE 聊天、记忆、审核、Markdown、工具和设置；处理请求 ID、错误及可选的全局 Bearer token。 |
| [`governanceApi.ts`](governanceApi.ts) | 共享治理的类型与 `/api/governance/*`、`/api/shared/*` 请求；同时发送可选全局 Bearer token 与页面提供的 `X-Agent-Key`。 |
| [`SharedGovernancePage.tsx`](SharedGovernancePage.tsx) | Agent 身份连接、共享空间与成员、提案批准/拒绝、有效共享记忆、沿革、撤销和审计。 |
| [`MemoryInspectDialog.tsx`](MemoryInspectDialog.tsx)、[`MemoryReviewPanel.tsx`](MemoryReviewPanel.tsx) | 「检查并纠正」对话框、纠正表单、当前内容、关联与版本时间线。 |
| [`MemoryReviewQueue.tsx`](MemoryReviewQueue.tsx) | 自动提取候选的原话入口、编辑与批准/拒绝。 |
| [`styles.css`](styles.css)、[`theme.css`](theme.css) | Tailwind 基础样式、应用布局、主题和响应式页面样式。 |
| [`icons.tsx`](icons.tsx) | 保留的本地 SVG 图标定义；当前页面直接使用 `lucide-react` 图标。 |

`main.tsx` 的来源跳转先查询消息来源，再选择对应会话并高亮消息。个人 API 与共享 API 分开，新增共享治理请求应走 `governanceApi.ts`，并传入当前页面中的 Agent key。

## 浏览器状态边界

- 个人记忆页使用 `sessionStorage` 记住 `library`、`review`、`storage` 子视图。`SELF.md` 和 `PENDING.md` 的未保存草稿也暂存在当前标签页；保存后删除，`MEMORY.md` 只读。
- 共享页的 Agent key、空间选择和查询结果是组件状态。页面在主导航切换时保持挂载，所以切走再回来仍可看到本次页面会话；浏览器刷新后需要重新输入 key。身份/空间切换、数据刷新和 401/403 失权响应会清理已显示的旧结果，并忽略过期的沿革响应。
- `VITE_MEMORIA_API_TOKEN` 来自 Vite 环境变量，属于浏览器可见的全局 API 配置，不是共享空间的 Agent key。具体配置见[前端总览](../README.md)。

可复用控件放在 [`components/ui/`](components/ui/README.md)；跨控件 class 合并函数在 [`lib/utils.ts`](lib/README.md)。
