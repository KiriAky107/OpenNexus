# NotesAgent Backend

NotesAgent Backend 是基于 Python 3.11+、FastAPI、Pydantic v2 和 SQLite 的本地 AI Core / Agent Core，使用 uv 管理 API 依赖和虚拟环境。

当前实现包含 Knowledge/Retrieval、Chat、Agent、Tool/Permission、Skill/Plugin、MCP、模型提供商、RAG Benchmark、多模态任务、本地模型调度、Token/音频用量和运行诊断。数据持久化位于后端 SQLite 与 Vault；Tauri Sidecar 生命周期、Stronghold 和操作系统级 Plugin 沙箱属于后续桌面阶段。

## 初始化与运行

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`uv sync` 会创建并管理 `backend/.venv`，无需手动激活环境。启动后可访问：

- 健康检查：<http://127.0.0.1:8000/health>
- 服务状态：<http://127.0.0.1:8000/api/status>
- API 文档：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

## 核心模块

| 目录 | 职责 |
| --- | --- |
| `app/knowledge`、`app/retrieval` | Markdown 解析、FTS5、sqlite-vec、RRF、真实 Embedding 路由和 Citation |
| `app/agent` | Agent Runtime、Tool 调用、权限与持久化 Trace |
| `app/extensions` | Skill、Plugin Host、MCP Registry 与 stdio/HTTP/SSE Bridge |
| `app/providers` | OpenAI Chat/Compatible、Responses、Anthropic Messages、Ollama 与能力路由 |
| `app/local_models` | 模型目录、固定 revision 下载、独立进程、设备回退和队列调度 |
| `app/services` | 索引、知识库上下文、聊天记录、转写、搜索历史、用量和诊断等应用服务 |
| `app/benchmarks` | 版本化 RAG Dataset、异步评测、指标与报告 |

## 模型路由

Embedding、音频转写和声纹匹配遵循同一规则：

1. 配置可用 API 时先调用 API；
2. API 失败或返回无效结果时回退本地模型；
3. 未配置 API 时直接使用本地模型；
4. `local_only` 请求只允许本地模型；
5. 响应和诊断记录实际来源、设备及回退原因。

生产向量按 Provider、模型、revision、接口和维度隔离，切换空间后需要重建索引。Markdown 和 FTS 在模型不可用时仍可保存与查询；`HashEmbeddingProvider` 仅供测试显式注入。

## 本地模型运行环境

API 的 `backend/.venv` 与模型环境分离。默认安装 CPU 运行组件：

```powershell
./scripts/install-model-runtime.ps1
```

可选 CUDA 环境：

```powershell
./scripts/install-model-runtime.ps1 -Device cuda -RuntimeDirectory ./.venv-models-cuda
$env:APP_MODEL_PYTHON = (Resolve-Path ./.venv-models-cuda/Scripts/python.exe).Path
```

脚本固定 `torch`/`torchaudio` 2.9.1，CUDA 使用 cu128 wheel，不安装驱动。其余模型依赖由 `scripts/model-requirements.lock` 锁定，包含 `qwen-asr`、`sentence-transformers`、ModelScope 和 PyAV。

| 能力 | 模型 | 固定 revision | 许可 |
| --- | --- | --- | --- |
| 默认 Embedding | `hotchpotch/bekko-embedding-v1-a8m` | `c721113d59a1d91b447450324f51c4b3332c924a` | MIT |
| 可选 Embedding | `ibm-granite/granite-embedding-97m-multilingual-r2` | `835ad14087e140460703cf0fae09f97d469d65c2` | Apache-2.0 |
| 音频转写 | `Qwen/Qwen3-ASR-0.6B` | `5eb144179a02acc5e5ba31e748d22b0cf3e303b0` | Apache-2.0 |
| 声纹匹配 | `iic/speech_eres2netv2_sv_zh-cn_16k-common` | `3317286545c587ae682dbc166831d9448780eebb` | Apache-2.0 |

模型运行时默认 CPU。任务在独立子进程中按需加载并在结束后释放；队列中查询 Embedding、媒体任务、后台索引的优先级依次降低。CUDA 不可用、初始化失败或显存不足时，系统清理失败进程并以同一冻结配置在 CPU 重试一次。

音频由 PyAV 解码为 16 kHz 单声道，经过能量分段、Qwen3-ASR 和 ERes2NetV2 片段聚类。当前只提供片段级时间戳，不支持逐字对齐、同段多人和重叠语音分离。

## Provider 与凭据

支持 OpenAI Chat/Compatible、OpenAI Responses、Anthropic Messages 和 Ollama。Provider 配置可分别绑定聊天、Embedding、转写和声纹能力，并通过受限的自定义请求 JSON 合并厂商扩展字段。

API Key 可由前端设置页写入，也可通过 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或 `AINOTE_CREDENTIAL_<ID>` 注入。开发环境使用 Fernet 密文存储，接口不返回明文；`plugin.*` 是 Plugin Settings 的保留凭据命名空间。

## 测试

```powershell
uv run pytest
```

当前基线为 562 项测试通过，另有一条既有 Starlette/httpx 弃用提示。真实模型冒烟脚本：

```powershell
.venv/Scripts/python scripts/local-model-smoke.py bekko --download
.venv/Scripts/python scripts/local-model-smoke.py qwen3-asr --download --audio C:/path/to/speech.wav
.venv/Scripts/python scripts/local-model-smoke.py eres2netv2 --download --audio C:/path/to/speech.wav --reference C:/path/to/reference.wav
```

机器可读接口以运行中的 `/openapi.json` 为准。

## 工作区保存与扩展恢复（2026-09-06）

HTTP 保存先写正文、元数据及 FTS，再调度后台向量更新；打开 Vault 的向量计算也不再阻塞入口。手动全量重建接口仍等待完成。待处理标记持久化，重新打开 Vault 可恢复处理；任务详情不是完整持久化队列。

扩展安装日志、ZIP 限制和社区包行为由自动化测试覆盖。
