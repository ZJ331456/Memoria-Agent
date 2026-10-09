# 记忆治理与多 Agent 共享记忆

[文档导航](README.md) · [API 接口](API接口文档.md) · [评测入口](../eval/README.md)

## 定位与已实现边界

Memoria 的聊天功能用于产生和使用个人记忆；新增加的共享记忆层面向多个 Agent 协作时的**受控事实库**。它把「谁提出、谁审核、谁能读、何时失效、为什么替换或撤销」作为数据模型的一部分。共享层可以独立于聊天调用，不需要模型服务即可运行。

当前实现是单机 SQLite、HTTP API 和本地治理工作台。它提供 Agent 独立密钥、空间与角色授权、先提案后发布、同主题版本冲突保护、有效期、撤销、来源指针和审计事件。它尚未将旧聊天运行时的全局 `memories` 检索改成多 Agent 检索。因此**隔离保证只适用于 `/api/shared/*` 与 `MemoryGovernance`**；旧 `/api/memories`、聊天工具、Markdown 导出和时间旅行接口仍是本地单用户数据域，不应用作多租户共享入口。

```mermaid
flowchart LR
  A[Agent key] --> S[空间授权检查]
  S --> P[提交提案 pending]
  P --> R[owner / curator 审核]
  R -->|批准 + 显式版本条件| M[共享记忆 active]
  R -->|拒绝| E[审计事件]
  M --> Q[按 Agent 与空间检索]
  M --> V[替代 / 过期 / 撤销]
  V --> E
  L[旧个人记忆] -->|管理员显式导入| P
```

## 数据与决策规则

| 对象 | 关键字段 | 规则 |
| --- | --- | --- |
| Agent | `id`、`name`、密钥哈希、`enabled` | 原始 key 创建时只返回一次；禁用后立即失去权限；管理员可轮换 key 恢复身份和原有空间权限。 |
| Space | `id`、`owner_agent_id`、`visibility` | `private` 只供所有者使用；`shared` 可授权其他 Agent。 |
| Grant | `space_id`、`agent_id`、`role` | `reader` 读当前记忆；`contributor` 可读并提交；`curator` 可读、提交、审核、撤销、看事件；只有 owner 管理授权。 |
| Proposal | 内容、类型、主题键、来源、有效期、状态、审核者与原因 | 所有写入先进入 `pending`；审批/拒绝不可重复处理。 |
| Governed memory | 内容、空间、主题键、版本、前驱、状态、来源 | 只召回已批准且当前有效的版本；旧版本保留供授权者查看版本链。 |
| Event | actor、动作、目标、原因、时间 | 记录授权、提案、审核、替代、撤销等治理动作；仅 owner/curator 可查。 |

`topic_key` 是同一空间内的稳定事实主题，例如 `project:alpha:database`。内容、类型、重要度、来源类型、来源 ID 或有效期发生变化时，审批方必须传入当前版本的 `expected_replaces_id`；它与当前版本不一致时返回 409，要求重新审核。完全相同的提案可确认已有版本。这样避免一个审核者覆盖另一审核者刚批准的更新，也防止来源或有效期更新被当成重复内容忽略。待审列表返回实时 `conflict_memory_id`，并保留提交时的 `submitted_conflict_memory_id`，供核对期间发生的变化。

审批事务会重新验证提交者仍启用、仍有该空间的提交权限；失权提案不能发布，治理者可以拒绝它。**来源指针是可追踪元数据，不等于自动验证过的证据。** `legacy_memory` 导入时和审批时均检查旧库来源存在且有效；其他 `source_type` 的真实性仍需审核者判断。

过期和撤销的记忆不进入共享检索；撤销当前版本会同时撤销该空间同主题的旧版本，普通成员不能用历史 ID 找回正文，治理者仍可审计。过期的过滤立即生效；审核详情和版本链用 `effective_status=expired` 表示过期，持久化状态和 `expire` 事件在后续同主题审批时写入，目前没有定时过期清理器。空间授权先于搜索与 ID 详情检查，未授权 Agent 无法借由记忆 ID、版本链或审计接口绕过隔离。私有空间不接受其他 Agent 的授权。

## 一次协作流程

以下命令以已配置 `[server.security].api_token` 为例；默认本地部署未配置时，管理员接口只允许回环地址访问。将返回的 Agent key 放在受保护的密钥管理位置，前端只在当前页面内存中保留粘贴的 key。

```bash
curl -X POST http://127.0.0.1:2237/api/governance/agents \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"research-agent"}'
```

响应中的 `token` 只在创建时出现。以该 key 调用 `/api/shared/*`：

```bash
curl -X POST http://127.0.0.1:2237/api/shared/spaces \
  -H "X-Agent-Key: $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"name":"项目决策","visibility":"shared"}'

curl -X POST http://127.0.0.1:2237/api/shared/proposals \
  -H "X-Agent-Key: $AGENT_KEY" -H 'Content-Type: application/json' \
  -d '{"space_id":"<SPACE_ID>","content":"Alpha 项目使用 PostgreSQL","topic_key":"alpha:database","source_type":"external","source_ref":"ADR-7"}'

curl -X POST http://127.0.0.1:2237/api/shared/proposals/<PROPOSAL_ID>/approve \
  -H "X-Agent-Key: $OWNER_OR_CURATOR_KEY" -H 'Content-Type: application/json' \
  -d '{"reason":"已核对 ADR-7"}'

curl 'http://127.0.0.1:2237/api/shared/memories?space_id=<SPACE_ID>&q=PostgreSQL' \
  -H "X-Agent-Key: $READER_KEY"
```

另一个 Agent 必须由 owner 经 `/api/shared/spaces/{id}/grants` 授予 `reader`、`contributor` 或 `curator`。把旧个人记忆移入共享空间时，管理员调用 `/api/governance/spaces/{space_id}/import-memory/{memory_id}` 并提供 owner 的 `actor_id`、`topic_key`；结果仍是待审提案，不会自动公开旧内容。

Agent key 丢失或泄露时，管理员调用 `POST /api/governance/agents/{agent_id}/rotate-key`。新 key 只返回一次，旧 key 立即失效；禁用的 Agent 会恢复启用，空间成员关系保持可管理。禁用与轮换会在关联空间留下管理员事件。工作台切换身份、切换空间或遇到失权响应时清空已加载内容，防止刷新后继续展示失效的本地缓存。

## 安全和容量边界

- `[server.security].api_token` 保护管理员与旧 API；Agent key 只用于 `/api/shared/*`。配置全局 token 时，带 `X-Agent-Key` 的共享路由由空间 ACL 单独认证；Agent key 不能访问旧个人记忆或管理员接口。
- 默认仅监听 `127.0.0.1`。公开部署应配置全局 token、TLS/反向代理和可信的网络边界；反向代理若把远端请求作为本机连接转发，不能依赖「回环地址管理员」例外。
- 共享检索目前是 SQLite 词面匹配与结果限制，适合小型团队知识库；还没有共享层的向量索引、跨节点并发部署、细粒度字段脱敏或 Agent 间来源证明。
- 撤销防止共享层再次检索，却不能让已被外部 Agent 复制到其提示词、日志或其他系统的数据消失。它也不清理旧聊天记忆，因为两层尚未合并。

## 公开评估与工程回归

共享治理已接入 [GateMem](https://github.com/rzhub/GateMem) 的 5 个公开检查点，覆盖效用、访问限制和主动遗忘；公开历史中的主体映射为逻辑 Agent，权限状态由 LLM 读取检查点之前的历史编译，再通过真实 `MemoryGovernance` 的提案、授权、审批、撤销、检索和详情 API 执行。

```bash
python -m eval.prepare_public --dataset gatemem
python -m eval.run_gatemem
```

这是独立评测适配器，审批代表基准导入操作，不是人工审核效果；权限编译不属于生产运行时，不证明旧个人聊天已经实现多主体隔离。报告只衡量五个原始检查点，不是官方 MGS 或完整集成绩；具体动作、回答、泄漏判定、来源与共享路径时延见 [公开报告](../eval/results/README.md)。GateMem 本身是上游合成 benchmark，而非现实机构记录。

共享 search/detail/source 的计时使用上述公开内容编译后的真实记忆，不再引用原先 1000 条自定义记录的微基准数字。权限、删除、共享读取可用 GateMem 衡量；长期 QA 和来源补充使用 LongMemEval/LoCoMo。[完整覆盖矩阵](./公开数据集评测方案.md)标明未测的工程机制。

旧二十四条治理夹具、独立评分器与合成微基准已移至 [tests/regression](../tests/regression/README.md)，三个旧合成分数报告已删除。工程测试继续覆盖密钥、跨连接并发审批、授权撤销、版本替代、来源失效与事务回滚；这些测试不构成公共任务泛化分数。
