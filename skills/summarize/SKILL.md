---
name: summarize
description: 总结 URL、会话历史或长文本。触发词：总结, 摘要, summarize, 这篇, 链接, 视频讲了什么, transcript
triggers: 总结, 摘要, summarize, 这篇, 链接, youtube, 转写, transcript
metadata: {"memoria":{"emoji":"🧾"}}
---

# Summarize（Memoria 适配）

改编自 akashic `summarize` skill。Memoria 不依赖外部 `summarize` CLI，改用内置工具完成。

## 何时使用

用户要求总结链接、文章、视频内容，或压缩一段对话/记忆时立即使用本技能。

## 流程

1. **对话/记忆总结**
   - 用 `search_history` 或 `recall_memory` 拉取相关材料
   - 输出固定结构：
     - 一句话结论
     - 关键要点（3-7 条）
     - 待办 / 未决问题（若有）
     - 可沉淀为长期记忆的候选（征求用户同意后再 `memorize`）

2. **URL / 网页总结**
   - 用 `http_get` 拉取正文（仅允许配置白名单主机；失败则说明原因）
   - 忽略导航、页脚和重复广告噪声
   - 长文先给 short 摘要，再询问是否展开某一节

3. **禁止**
   - 不要伪造未拉取到的网页内容
   - 不要把助手猜测升级成用户事实后直接写入记忆

## 输出长度

默认 short；用户说“详细/完整”时再给 medium。
