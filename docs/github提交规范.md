
# Git Commit Message Rules

为了保持项目 Git 提交记录清晰、规范、易维护，所有 Commit Message 请遵循以下提交规范，远程仓库链接：https://github.com/ZJ331456/Memoria-Agent。

Commit Message 采用 **Conventional Commits** 风格：

---

# 1. Commit Message 格式

统一格式：

```bash
<type>: <description>
```

其中：

- `<type>`：提交类型，使用固定英文关键词
- `<description>`：具体修改内容，采用**中文为主 + 英文技术名词辅助**的描述方式

示例：

```bash
feat: 新增 Qwen3 inference 推理支持
fix: 修复 training 阶段 CUDA memory 泄漏问题
docs: 更新 README 安装和使用说明
```

---

# 2. Commit Type 说明

| Type | 说明 | 示例 |
|------|------|------|
| feat | 新功能开发 | `feat: 新增 graph retrieval 检索模块` |
| fix | 修复问题 / Bug | `fix: 修复 CUDA memory error 问题` |
| docs | 文档修改 | `docs: 更新 installation 使用文档` |
| style | 代码格式调整（不影响功能） | `style: 统一 python code 格式规范` |
| refactor | 代码重构 | `refactor: 重构 data loader 数据处理流程` |
| perf | 性能优化 | `perf: 优化 vLLM inference 推理速度` |
| test | 添加或修改测试 | `test: 新增 retriever 单元测试` |
| chore | 构建、依赖、配置等杂项修改 | `chore: 更新 requirements 依赖版本` |
| build | 构建系统修改 | `build: 更新 Docker 配置文件` |
| ci | CI/CD 流程相关修改 | `ci: 添加 GitHub Actions 自动测试流程` |

---

# 3. 常用提交示例

## 新增功能 Feature

```bash
git commit -m "feat: 新增 hybrid retrieval 混合检索 pipeline"
```

---

## 修复 Bug

```bash
git commit -m "fix: 修复 entity matching 匹配错误问题"
```

---

## 更新文档

```bash
git commit -m "docs: 更新 quick start 快速开始说明"
```

---

## 性能优化

```bash
git commit -m "perf: 优化 vLLM batch inference 推理效率"
```

---

## 代码重构

```bash
git commit -m "refactor: 重构 model architecture 模型结构"
```

---

# 4. Branch 命名规范

推荐使用以下分支命名：

| 类型 | 格式 | 示例 |
|-|-|-|
| 新功能开发 | `feature/*` | `feature/qwen3-support` |
| Bug 修复 | `bugfix/*` | `bugfix/tokenizer-error` |
| 实验开发 | `exp/*` | `exp/rag-ablation` |
| 文档修改 | `docs/*` | `docs/readme-update` |

创建分支：

```bash
git checkout -b feature/new-module
```

---

# 5. Pull Request 标题规范

PR 标题保持与 Commit Message 风格一致：

格式：

```text
<type>: <description>
```

示例：

```text
feat: 新增 Qwen3-8B backend 支持
```

---

# 6. 提交注意事项

## 推荐提交方式

✅ 描述清晰，说明具体修改内容：

```bash
feat: 新增 reranker 重排序模块
```

✅ 一个 Commit 尽量完成一个主要任务：

例如：

```text
feat: 新增 retrieval module
```

不要同时包含：

```text
feat: 新增 retrieval + 修改 training + 更新 docs
```

---

## 避免无意义提交

❌ 不推荐：

```bash
update
modify
fix
test
aaa
```

原因：

- 无法了解修改内容
- 不利于代码回溯
- 不符合团队协作规范

---

# 7. Git 提交流程

查看修改：

```bash
git status
```

---

添加文件：

```bash
git add .
```

---

提交：

```bash
git commit -m "feat: 新增 xxx 功能"
```

---

推送：

```bash
git push
```

---

# 8. 大文件提交规范

以下文件不要直接提交到 GitHub：

- 模型文件

```
*.pth
*.pt
*.ckpt
*.safetensors
```

- 数据集文件

```
datasets/
data/
```

- 日志文件

```
logs/
wandb/
```

推荐存储方式：

- HuggingFace
- ModelScope
- Git LFS

---

# 9. 推荐项目 Commit 示例

```text
feat: 初始化 project 项目结构

feat: 新增 graph retrieval 检索 pipeline

feat: 增加 Qwen3 inference 推理 backend

perf: 优化 vLLM batch inference 性能

fix: 修复 CUDA memory overflow 问题

docs: 更新 deployment 部署文档

refactor: 重构 dataset loader 数据加载模块
```

---

# Summary

一个规范的 Git Commit 应满足：

- 使用固定 type
- description 中文为主，英文技术名词辅助
- 简洁明确描述修改内容
- 一个 Commit 对应一个主要修改

推荐格式：

```bash
feat: 新增 xxx 功能

fix: 修复 xxx 问题

docs: 更新 xxx 文档

perf: 优化 xxx 性能

refactor: 重构 xxx 模块
```

---

# 推荐提交风格

适用于 AI / RAG / Agent / 深度学习项目：

```bash
feat: 新增 Qwen3-8B inference 支持

feat: 添加 Graph RAG 检索流程

fix: 修复 vLLM batch inference 显存问题

perf: 优化 embedding 模型推理速度

docs: 更新 README 使用说明

refactor: 重构 evaluation 评测模块
```

---

# 附录：第十三轮变更提交记录（Bi-temporal + Agentic Memory）

本轮按规范提交（一个 Commit 对应一个主要修改）：

```bash
feat: 新增记忆 bi-temporal 版本治理 valid_at/invalid_at 与时间旅行查询
feat: 新增记忆图交叉链接与检索图遍历增强
feat: 新增记忆持续演化 evolve 与演化审计
docs: 新增第十三轮 BiTemporal 与 AgenticMemory 设计文档
test: 新增时间旅行与记忆演化回归用例
```
