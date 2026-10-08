# 通用工具

[返回源码导航](../README.md) · [UI 基础组件](../components/ui/README.md) · [前端总览](../../README.md)

[`utils.ts`](utils.ts) 目前只导出 `cn(...inputs)`：先用 `clsx` 处理条件类名，再用 `tailwind-merge` 合并冲突的 Tailwind 类。UI 基础组件通过 `@/lib/utils` 使用它，使调用方传入的 `className` 能覆盖部分默认样式。

接口请求与类型分别在 [`api.ts`](../api.ts)、[`governanceApi.ts`](../governanceApi.ts)。这个目录没有保存身份凭据或页面草稿；相关状态边界见[源码导航](../README.md)。
