# 后端测试导航

[项目首页](../README.md) · [后端模块](../memoria/README.md) · [评测目录](../eval/README.md) · [浏览器测试](../frontend/e2e/README.md)

本目录验证 Python 存储、个人 Agent 运行时和共享记忆治理。API 测试主要使用 FastAPI TestClient，模型响应使用假实现或 MockTransport；SQLite 测试使用临时目录。浏览器交互测试在 `frontend/e2e/`，独立评测器测试在 `tests/regression/test_governance_eval.py`。

## 安装与运行

以下命令在仓库根目录执行；使用虚拟环境时将 `python` 替换为 `.venv/bin/python`。

```bash
python -m pip install -r requirements.txt
python -m pip install pytest
python -m pytest -q
```

全部后端与评分器回归位于 `tests/`，包括 `tests/regression/`；`eval/` 仅保留公共数据集适配与结果。测试数量会随参数化与新用例变化，以本次 pytest 输出为准。

## 按能力查找

| 文件 | 覆盖内容 |
| --- | --- |
| [test_core.py](test_core.py) | 会话/记忆 CRUD、用户纠正、版本链、重复抑制、语义回填、生命周期顺序、上下文预算、工具循环、错误分类与 trace 脱敏 |
| [test_optimizations.py](test_optimizations.py) | 持久化抽取任务、租约恢复、撤销、FTS、查询门控、工具权限、SSE、API 认证/Origin/请求体限制及可选向量后端 |
| [test_memory_review.py](test_memory_review.py) | 自动提取先待审、批准后召回、旧消息来源定位和处理中候选重启恢复 |
| [test_memory_retrieval_scale.py](test_memory_retrieval_scale.py) | 较早低重要度记忆召回、替代版本过滤、问句尾词和无事实线索查询 |
| [test_episodic_memory.py](test_episodic_memory.py) | 情景来源校验、幂等、相关性、TTL/固定保留、管理归档、维护预览和级联删除 |
| [test_layered_context_budget.py](test_layered_context_budget.py) | 分区/整帧限额、伪造标题边界、超长用户/系统、工具协议和并发读取配额 |
| [test_incremental_compaction.py](test_incremental_compaction.py) | 500 条以上历史、partial 续读、真实覆盖、CAS 并发、摘要限额与来源失效清理 |
| [test_memory_layers_integration.py](test_memory_layers_integration.py) | 运行时召回/技能 trace、失败/取消、API 认证/开关、删除活跃会话、来源审批竞态及紧急预算 |
| [test_layer_settings_config.py](test_layer_settings_config.py) | 分层配置继承、严格字段类型和完整技能文件的版本指纹 |
| [test_review_source_transactions.py](test_review_source_transactions.py) | 来源失效时拒绝创建/替代/强化，跨连接删除竞态与外层事务回滚 |
| [test_memory_layers_eval.py](test_memory_layers_eval.py) | 离线真实模块评估结果，以及故障注入后 Runner 的失败退出码 |
| [test_embedding_client.py](test_embedding_client.py) | 供应商十条批量限制、完整覆盖和向量排序 |
| [test_public_benchmarks.py](test_public_benchmarks.py) | 公开适配器的未来/标签隔离、真实 ACL/审批/撤销、人物与来源、错误分母 |
| [test_longmemeval.py](test_longmemeval.py) | 标签隔离、逐题数据库隔离、来源指标、向量缓存、失败分母与正式回答校验 |
| [test_governance_core.py](test_governance_core.py) | Agent 密钥、空间 ACL、冲突比较、生命周期、来源校验、密钥恢复、失权提案及跨连接并发审批 |
| [test_governance_api.py](test_governance_api.py) | 管理员与 Agent key 认证边界、共享审核/撤销/版本以及旧库导入 |
| [test_store_transactions.py](test_store_transactions.py) | 个人记忆元数据写入不得提前提交共享连接上的治理事务；创建与替代两个分支 |
| [test_round7_akashic.py](test_round7_akashic.py) | Context Frame、会话压缩、事件上下文、工具并行、中断记录与 Markdown 导出 |
| [test_skills.py](test_skills.py) | 技能扫描、触发词匹配、加载 API、内置目录和 HTTP 主机匹配 |
| [test_round9_setup_markdown.py](test_round9_setup_markdown.py) | 模型覆盖配置、Setup API、Markdown 同步和自我档案读写 |
| [test_round10_tool_search_mcp.py](test_round10_tool_search_mcp.py) | 工具按需暴露、MCP 命名、stdio demo 往返和 API 重载 |
| [test_round11_drift.py](test_round11_drift.py) | 空闲判断、静默时段、开关、任务记录、同技能续跑和技能存在性 |
| [评测器回归](regression/test_governance_eval.py) | 漏报、来源泄漏、错误会话、模板、输入校验与真实治理 Runner |

`round*` 文件名保留演进历史；当前能力按表中职责定位，无需按轮次顺序执行。

## 常用定向命令

```bash
# 共享治理与数据库事务
python -m pytest -q tests/test_governance_core.py tests/test_governance_api.py tests/test_store_transactions.py

# 个人记忆审核与检索
python -m pytest -q tests/test_memory_review.py tests/test_memory_retrieval_scale.py

# 独立评测器
python -m pytest -q tests/regression/test_governance_eval.py

# 分层记忆与生命周期
python -m pytest -q tests/test_episodic_memory.py tests/test_layered_context_budget.py \
  tests/test_incremental_compaction.py tests/test_memory_layers_integration.py \
  tests/test_layer_settings_config.py tests/test_review_source_transactions.py tests/test_memory_layers_eval.py

# 定位慢用例或只选择一个能力
python -m pytest -q --durations=5
python -m pytest -q tests/test_core.py -k user_correction
```

## 依赖与边界

- `sqlite-vec` 是可选依赖。未安装时对应 KNN 用例会 skip，其他检索走 JSON 后端；要验证向量扩展，先安装 `requirements-vector.txt`。
- MCP demo 用例启动本地 Python stdio 子进程；部分 API 配置也会读取默认 MCP 配置。排查慢测试时检查本机 `data/mcp_servers.json`，这些测试并非每项都零进程或完全隔离个人 MCP 配置。
- 不需要真实模型密钥即可运行核心回归；这不等于测过真实供应商兼容性、摘要质量、提取质量或嵌入模型性能。
- 权限隔离测试针对共享记忆服务，不证明旧聊天运行时已经支持多租户。
- 不将单元测试耗时视为服务延迟。性能基准与实际系统输出评分分别见 eval README。

自定义夹具、合成存储微基准与机制 Runner 的迁移命令见 [regression 导航](regression/README.md)，它们不产生公开 benchmark 成绩。
