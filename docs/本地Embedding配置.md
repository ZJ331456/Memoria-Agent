# 本地中文 Embedding：BGE small zh v1.5

本机方案使用项目根目录 `model/bge-small-zh-v1.5/` 存储模型，由项目 `.venv` 进程内编码。本地接入属于尚未提交的工作区能力，不能视为远程 main 已有状态。main 保留既有 DeepSeek `deepseek-v4-flash`，fast 保留原有 Qwen `qwen3.6-flash` 配置；本次验证没有调用付费 LLM API。模型文件、环境、私有 override 与原始报告均为 Git 忽略项。

## 文件与环境

```text
Memoria-Agent/
├─ model/bge-small-zh-v1.5/      # Safetensors、tokenizer、配置与下载校验清单
├─ .venv/                      # 独立 Python 环境，不继承 base site-packages
├─ data/models.override.toml   # 本机模型槽位配置，含凭据，已忽略
├─ scripts/download_embedding.py
└─ scripts/check_local_embedding.py
```

下载源为 [ModelScope BAAI/bge-small-zh-v1.5](https://www.modelscope.cn/models/BAAI/bge-small-zh-v1.5)。默认固定提交 `8399f11f8da998fe932df2684586c92024219d05`，选择 12 个模型/配置文件，合计 96,406,042 字节（约 91.94 MiB）。权重文件 `model.safetensors` 是 95,827,648 字节；重复的 `.bin` 权重不下载。

“12 文件 / 96,406,042 字节”只统计所选远端模型与配置文件；ModelScope 本地 SDK 的 `.msc` / `.mv` 元数据及额外生成的 `download_manifest.json` 不计入该口径。

ModelScope 1.34 的 snapshot 下载器只接受分支/标签。脚本先核验固定提交与公开分支的选中文件清单相同，再下载，在下载后重新核验远端清单和每个文件的 SHA256/大小；`download_manifest.json` 明确区分指定提交和下载分支。已有文件可复用缓存。

本机使用 Python 3.13、PyTorch 2.9.0 CUDA 12.6 与 Transformers 5.5.3。以下命令均在项目目录执行，用 `.venv` 的解释器，无需激活环境，也不向 conda base 安装依赖：

```powershell
# 新机器首次创建；已有 .venv 无需重复创建。
python -m venv .venv

# CUDA PyTorch 先使用官方索引安装；本机 RTX 4060 使用 CUDA 12.6 wheel。
.\.venv\Scripts\python.exe -m pip install "torch==2.9.0" --index-url https://download.pytorch.org/whl/cu126
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-local-embedding.txt pytest
.\.venv\Scripts\python.exe scripts/download_embedding.py
```

CUDA wheel 包含所需运行库，正常使用不需要另外安装完整 CUDA Toolkit。其他机器应先确认显卡驱动与所选 wheel 的兼容性。[PyTorch 官方安装说明](https://pytorch.org/get-started/previous-versions/)

检查独立环境：

```powershell
.\.venv\Scripts\python.exe -c "import sys, torch; print(sys.executable); print(sys.prefix != sys.base_prefix); print(torch.__version__); print(torch.cuda.is_available())"
Get-Content .venv/pyvenv.cfg
```

`include-system-site-packages` 应为 `false`。项目不会修改 conda base 的依赖。

本机实际环境见[环境报告](../data/reports/local_embedding_environment.json)：Python 3.13.5、PyTorch 2.9.0+cu126、Transformers 5.5.3、SQLite 3.50.2，独立 `.venv` 不继承 base site-packages；`pip check` 已通过。向量后端配置为 `auto`，`sqlite-vec` 未安装，不能声称验证了该扩展。报告只保留模型槽位状态，不包含 API Key。

## 配置与启动

本机实际配置通过已有 `data/models.override.toml` 保存，Embedding 槽位为：

```toml
[memory.embedding]
model = "model/bge-small-zh-v1.5"
base_url = "local://cuda"
```

本地后端无需 API Key；旧 Embedding key 即使仍存于私有配置中也不会用于本地编码。`local://cuda` 明确要求 GPU，不可用时报告错误；`local://cpu` 明确走 CPU；`local://auto` 自动选择可用设备。模型相对路径按项目根目录解析。

使用独立环境启动后端：

```powershell
.\.venv\Scripts\python.exe main.py
```

已有后端进程需要用此解释器重启后才会应用代码和启动配置。前端的模型设置页也支持保存这两个字段并测试本地编码；如果后端使用的是其他解释器，切换设置不能自动安装 CUDA 依赖。

应用启动时先预热本地模型再开始接收请求，以免首次编码加载超过普通请求超时。预热独立使用 `max(120 秒, 常规 timeout_seconds)`，不修改之后的请求超时；远程模型不会被这一步调用。预热失败会报告错误，应先检查设备、模型路径和依赖。前端本地 Embedding 提示“本地推理 · 无需 API Key”；main/fast 仍按实际密钥状态判断配置是否就绪。

## 编码约定与索引隔离

当前 `local://` 后端针对该中文 small 模型实现。按 [BGE 官方模型卡](https://huggingface.co/BAAI/bge-small-zh-v1.5) 的检索约定，使用 512 维 CLS 向量、FP32 推理与 L2 归一化，每次输入最多 512 个 tokenizer token。

加载时如出现唯一的 `embeddings.position_ids` 为 `UNEXPECTED` 的提示，这是该 checkpoint 与当前 Transformers 非持久 buffer 的兼容提示。本机已核验该 tensor 为 `(1, 512)`，内容与 `arange(512)` 一致；当前 `BertEmbeddings` 将它注册为 `persistent=False`，因此不是模型权重缺失。此说明只针对已核验的这一个 key；其他加载异常仍需单独检查。

- 查询通过 `embed_query` 添加 `为这个句子生成表示以用于检索相关文章：`；记忆写入、纠正和索引回填不添加查询指令。
- 模型和 tokenizer 只从本地读取，`local_files_only=True`、`trust_remote_code=False`；推理不访问模型平台或远程 Embedding API。
- 进程中复用已加载模型，单个模型/设备使用一个串行工作线程。编码移出 asyncio 事件循环；最多 8 个运行/待运行任务，超出会报错。超时会取消尚未开始的任务，已经开始的 CUDA 任务运行结束后释放占位。
- 向量命名空间包含实际权重、tokenizer/配置内容与编码策略指纹，不包含 API Key 或设备名。创建客户端后、首次加载前如文件发生变化，加载会拒绝，避免新权重被写到旧命名空间。

模型切换后，原远程 Embedding 向量保留在自己的命名空间，不参与 BGE 相似度计算；新 BGE 向量按现有回填/重新索引流程生成。正式实验要使用隔离数据库并冻结模型文件；更新模型建议下载到新目录后切换配置。当前项目仍在读取时做有限回填，此次接入没有完成实验计划中的后台索引改造。

**输入截断和相似度阈值仍需实验校准。** 当前后端在 512 token 处截断；长事件/长 Markdown 分块尚需按 tokenizer 重做。原检索引擎的相似度阈值仍保留，不能把接入成功当作记忆质量已经提高。该模型主要用于中文；英文公开基准应另固定适合语言的模型。

## 离线验证与下一步

```powershell
.\.venv\Scripts\python.exe scripts/check_local_embedding.py --device cuda --output data/reports/local_bge_cuda_smoke.json
.\.venv\Scripts\python.exe scripts/check_local_embedding.py --device cpu --output data/reports/local_bge_cpu_smoke.json
.\.venv\Scripts\python.exe -m pytest tests/test_local_embedding.py tests/test_embedding_client.py tests/test_memory_consistency.py tests/test_core.py tests/test_round9_setup_markdown.py tests/test_layer_settings_config.py -q
```

检查脚本用临时数据库验证 512 维、非零有限向量、单位范数、简单中文查询排序、索引回填、真实 `MemoryEngine.retrieve` 和模型测试 HTTP 路由；会输出首批含加载耗时、少量暖查询耗时、PyTorch tensor 分配显存及运行环境。它不会修改个人记忆库，也不会调用 LLM API。

2026-10-10 本机实际结果：

| 检查 | 实际结果 |
| --- | --- |
| 模型下载 | 固定 revision `8399f11f8da998fe932df2684586c92024219d05`，12 文件、96,406,042 字节；本地文件大小与 SHA256 已核验 |
| CUDA 编码/临时库/API smoke | 首批 4 句含加载 **49.295 秒**；8 次暖查询 median **3.015 ms**；4 条索引回填、2 次真实查询和 Embedding HTTP 模型测试通过；[CUDA 报告](../data/reports/local_bge_cuda_smoke.json) |
| CPU 编码/临时库/API smoke | 首批 4 句含加载 **7.737 秒**；8 次暖查询 median **5.644 ms**；相同接线检查通过；[CPU 报告](../data/reports/local_bge_cpu_smoke.json) |
| 新鲜进程 API 启动 | 含本地预热 **8.545 秒**，Embedding 模型测试返回 512 维并通过；[启动报告](../data/reports/local_bge_startup_api_smoke.json) |
| 定向回归 | 上述 6 文件最后复跑 **65 passed、1 skipped，6.32 秒**；`sqlite-vec` 未安装，相关测试跳过，该后端未验证 |
| 前端本机依赖/构建 | `frontend` 下 `npm ci --no-audit --no-fund` 与 `npm run build` 通过；仅项目内依赖，`package.json` / 锁文件未改 |
| LLM API | 上述 smoke 报告均记录 `llm_api_calls=0`；保留配置不代表已测付费 LLM 连通性或对话效果 |

CPU/CUDA 报告的向量命名空间一致，为 `embedding-local:fb2b88ca2ec04dc58274575b1e74358195015e24f8ecc3e98c1a8c5b32f9de65`。GPU 首批耗时包含 torch 首次 import 和加载；CPU 在 GPU 测量后运行、文件缓存已热，新鲜进程 API 启动也使用已热的文件缓存。因此这些首批数字不能直接用于 CPU/GPU 冷性能比较，启动数字也不是冷文件系统的保证。GPU 的 PyTorch peak allocated **102.97 MiB** 仅为 tensor 分配峰值，不等于完整进程、CUDA 上下文与驱动的总显存占用。

这些只是接线检查，4 条合成记忆、2 个查询不能代表长期陪伴质量或检索质量提升；每设备 8 次暖查询不能当作 p95 SLO。token-aware 分块、开发集阈值校准、完整实验 manifest 与 LLM usage 记录仍待完成。后续按 [长期陪伴记忆研究与实验计划书 v1.2](长期陪伴记忆研究与实验计划书.md) 执行正式中文 50-query 试测、240-query 产品验证、更新机制与访问消融；这些正式实验本次尚未运行。
