# 浏览器端到端测试

[返回前端总览](../README.md) · [源码导航](../src/README.md)

测试写在 [`app.spec.ts`](app.spec.ts)。[`playwright.config.ts`](../playwright.config.ts) 只配置 Chromium，测试时以 `npm run dev -- --host 127.0.0.1 --port 4173` 启动 Vite，`baseURL` 为 `http://127.0.0.1:4173`。非 CI 环境允许复用已在此地址运行的服务器；CI 重试两次，并在首次重试时记录 trace。

在 `frontend/` 下执行：

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

如果环境中已有可用的 Chromium 可执行文件，可设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/path/to/chromium` 后运行测试；配置会把该路径交给 Playwright。`npm run build` 是单独的 TypeScript 与 Vite 构建检查。

## 模拟边界与当前覆盖

`app.spec.ts` 在每个测试打开页面前以 `page.route('**/api/**')` 拦截 API。测试返回固定 JSON 或 SSE，未模拟的路径返回 404；因此它验证浏览器交互、页面状态和前端请求流程，不验证真实后端、模型调用、数据库权限实现或代理连通性。后端服务无需为此套测试启动。

当前九个场景覆盖：流式聊天时应用外壳保持可见；记忆任务重试与撤销预览/确认；记忆时间线和移动导航；纠正理由与版本；候选审核及来源跳转；Markdown 草稿跨页面保留并在保存后清除；设置请求失败不遮挡会话；共享治理从 Agent、空间、授权、提案到审核与审计的流程及 key 不入浏览器存储；访问失效后清除旧共享内容并忽略迟到的沿革响应。修改 API 合约或页面状态时，应同步调整这里的模拟数据与断言。
