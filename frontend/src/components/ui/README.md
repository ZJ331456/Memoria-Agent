# UI 基础组件

[返回组件目录](../README.md) · [源码导航](../../README.md) · [前端总览](../../../README.md)

本目录是项目使用的本地 TSX 控件层，按 [`components.json`](../../../components.json) 的 `base-nova`、Base UI、Tailwind 和 `@/` 路径约定组织。业务页面可从 `@/components/ui/<文件名>` 导入，不从页面复制一份通用控件。

| 用途 | 现有文件 |
| --- | --- |
| 表单与操作 | `button.tsx`、`field.tsx`、`input.tsx`、`textarea.tsx`、`select.tsx`、`label.tsx` |
| 容器与反馈 | `card.tsx`、`alert.tsx`、`alert-dialog.tsx`、`badge.tsx`、`empty.tsx`、`spinner.tsx`、`separator.tsx`、`table.tsx`、`tabs.tsx` |
| 对话内容 | `message.tsx`、`message-scroller.tsx`、`bubble.tsx` |
| 装饰 | `dot-pattern.tsx`、`magic-card.tsx` |

组件通常接收原生元素属性和 `className`，使用 [`cn()`](../../lib/README.md) 合并 Tailwind 类；部分组件暴露 `data-slot` 供样式定位。使用前以对应文件实际导出的属性和变体为准，例如 `Button` 提供 `default`、`outline`、`ghost`、`destructive` 四种 `variant` 与 `default`、`sm`、`icon` 三种 `size`。页面布局和治理工作台专属样式在 [`theme.css`](../../theme.css)，全局基础样式在 [`styles.css`](../../styles.css)。
