# Skill Creator：编写技能说明

[技能目录与格式](../README.md) · [运行时指令](SKILL.md) · [加载器](../../memoria/skills/README.md)

用于指导创建或改写 Memoria 自身的技能，触发词包括“创建技能”“写技能”、`skill` 和 `SKILL.md`。它是一个编写流程，不是自动创建文件的插件。

## 交付结构

```text
skills/<name>/
├── README.md       # 开发者说明
└── SKILL.md        # 运行时读取的指令
```

可以保存额外参考材料，但当前加载器只读取 SKILL.md，不自动递归加载 references 或 README。运行时没有 shell / write_file；生成的内容需要在开发环境中落盘，再重载目录。

编写时声明准确的触发词、依赖和已有工具名称，说明失败时的处理；不要假设存在 Akashic 插件 API、未注册的工具或复杂 YAML 解析能力。正文宜短，长材料避免占用每轮上下文。

## 使用与验证

示例：“帮我设计一个发布复盘技能，先给出 SKILL.md 内容。”

从仓库根目录运行：

```bash
python -m pytest -q tests/test_skills.py
```

随后通过 `POST /api/skills/reload` 和 `GET /api/skills` 检查新增项；使用全局 token 的服务需要附认证头。
