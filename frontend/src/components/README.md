# 组件目录

[返回源码导航](../README.md) · [UI 基础组件](ui/README.md) · [前端总览](../../README.md)

目前此目录存放 [`ui/`](ui/README.md) 中的通用展示与交互控件。页面级组合仍位于 `src/`：个人记忆检查与候选审核见 [`MemoryInspectDialog.tsx`](../MemoryInspectDialog.tsx)、[`MemoryReviewPanel.tsx`](../MemoryReviewPanel.tsx)、[`MemoryReviewQueue.tsx`](../MemoryReviewQueue.tsx)；共享治理工作台见 [`SharedGovernancePage.tsx`](../SharedGovernancePage.tsx)。主导航与其他页面组合见 [`main.tsx`](../main.tsx)。

增加页面时先复用 `ui/` 中的按钮、卡片、表单字段、提示和对话框。页面专有的 API 状态、权限判断和请求操作放在页面组件或相应 API 模块中；基础控件只处理通用交互与样式。
