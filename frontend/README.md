# Memoria 前端

[项目首页](../README.md) · [后端 API](../memoria/api/README.md) · [源码导航](src/README.md)

这是 Memoria-Agent 的 Vite、React 19 与 TypeScript 工作台。主导航包含对话、记忆、共享治理、追踪、工具和设置六个入口；桌面侧栏与移动端导航共用同一组页面。

## 页面与代码

| 页面 | 当前交互 | 主要代码 |
| --- | --- | --- |
| 对话 | 会话列表、流式回复、停止生成、定位来源消息 | [`src/main.tsx`](src/main.tsx)、[`src/api.ts`](src/api.ts) |
| 记忆 | 有效记忆检索与纠正、自动提取候选审核、Markdown 视图和任务 | [`src/main.tsx`](src/main.tsx)、[`src/MemoryInspectDialog.tsx`](src/MemoryInspectDialog.tsx)、[`src/MemoryReviewQueue.tsx`](src/MemoryReviewQueue.tsx) |
| 共享治理 | Agent 身份、空间授权、提案审核、共享记忆、沿革和审计 | [`src/SharedGovernancePage.tsx`](src/SharedGovernancePage.tsx)、[`src/governanceApi.ts`](src/governanceApi.ts) |
| 追踪、工具、设置 | 运行记录、工具与 MCP 状态、模型配置 | [`src/main.tsx`](src/main.tsx)、[`src/api.ts`](src/api.ts) |

记忆库中的「检查并纠正」会打开对话框，显示当前内容、纠正表单和按需读取的版本时间线；候选审核在记忆页的独立视图中。来源 ID 可定位到原始对话。布局、响应式样式和视觉主题分别见 [`src/styles.css`](src/styles.css) 与 [`src/theme.css`](src/theme.css)。更细的目录说明见[源码导航](src/README.md)、[组件说明](src/components/README.md)和[浏览器测试](e2e/README.md)。

详情的 `validity_intervals` 可包含多段生效记录；撤销来源后恢复旧记忆时，纠正窗口展示各段区间，保留中间版本曾生效的历史。该展示与版本时间线分别对应有效时间和内容替代关系。

## 本地开发

在 `frontend/` 目录执行：

```bash
npm ci
npm run dev
```

浏览器打开 Vite 输出的地址。开发服务器将 `/api` 代理到 `http://127.0.0.1:2237`；使用真实功能时需先启动该地址上的后端。`@/` 指向 `src/`。

```bash
npm run build
```

`build` 先运行 TypeScript 项目构建检查，再运行 Vite 生产构建。

后端启用 `[server.security].api_token` 时，可将 [`.env.example`](.env.example) 复制为 `.env.local`，填写 `VITE_MEMORIA_API_TOKEN`。`src/api.ts` 和 `src/governanceApi.ts` 都会将该值作为 `Authorization: Bearer` 发送；Vite 会把 `VITE_` 变量交给浏览器，因此它是前端可见配置。共享治理还需要单独的 `X-Agent-Key`：在页面创建 Agent 后复制一次返回的 token，或粘贴已有 key。Agent key 只保存在当前 React 页面内存中，刷新后需要重新输入，不写入 localStorage 或 sessionStorage。全局 API token 与 Agent key 分别由后端用于服务访问和 Agent 身份。

## 页面状态与测试

个人记忆页的子视图选择保存在当前标签页的 sessionStorage。`SELF.md`、`PENDING.md` 的未保存编辑也按文件名暂存于 sessionStorage，保存后删除草稿；`MEMORY.md` 是只读视图。有效记忆、审核和任务以服务端数据为准。共享治理页在已挂载的页面内存中缓存空间、提案、记忆、授权、审计和沿革；切换身份或空间、刷新数据及收到 401/403 失权响应时会清理旧结果，刷新浏览器会清空这些页面状态。

```bash
npx playwright install chromium
npm run test:e2e
```

浏览器测试使用模拟 `/api` 响应，不需要真实模型或后端。测试命令、端口、覆盖范围与浏览器路径配置见 [E2E 说明](e2e/README.md)。
