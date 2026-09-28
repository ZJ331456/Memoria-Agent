# Memoria 前端

Vite + React + TypeScript 前端，组件基于 shadcn Nova/Base UI 结构。聊天使用 MessageScroller/Message/Bubble，记忆运维使用 Card/Table/Badge/AlertDialog。

界面使用 `src/theme.css` 统一视觉主题、桌面侧栏和移动端五入口导航。记忆页先展示摘要卡片，点击“查看时间线”才读取完整版本链。独立 API 请求失败时，其他已成功加载的面板仍可使用；侧栏连接状态来自 `GET /api/overview`。

## 开发与构建

```bash
npm install
npm run dev
npm run build
```

后端启用 `[server.security].api_token` 时，复制 `.env.example` 为 `.env.local` 并设置 `VITE_MEMORIA_API_TOKEN`。该值会进入浏览器环境，因此只适合个人本地或受控内网；公网多用户部署应改用服务端会话认证。

## 浏览器测试

```bash
npx playwright install chromium
npm run test:e2e
```

测试使用浏览器 API mock，不调用真实模型，覆盖流式消息不白屏、失败记忆任务重试、撤销预览和确认撤销。CI 产物目录 `playwright-report/`、`test-results/` 已被 Git 忽略。
