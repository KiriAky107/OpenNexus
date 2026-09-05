# Notes Agent（暂命名） 团队开发说明

> 本文件用于团队开发期间快速配置环境、启动项目并了解当前实现状态，不是正式的项目 README。

NotesAgent 是本地优先的 AI 笔记与知识库项目。当前可运行形态为 Vue/Vite Web 前端与 FastAPI AI Core：Markdown 和附件保存在本地 Vault，SQLite 管理元数据、全文索引、向量空间、搜索历史、AI 会话、任务、Agent Trace、多模态任务及运行诊断。AI 对话已接入知识库检索，会话与消息由后端持久化并供 Web 和桌面客户端共用。

截至 2026-09-05，第一阶段及第二阶段 A～F 的工程范围已经合并到 `main`。当前已完成真实 Workspace、混合检索与知识库问答、Agent/Tool/Permission、Skill/Plugin、MCP 配置与调用、模型提供商与路由、RAG Benchmark，以及本地 Embedding、音频转写和片段级声纹聚类。Tauri/Rust Host、Stronghold、原生多 Vault 文件系统、生产级 MCP 沙箱和 Sync Server 尚未接入。

## 目录

```text
NotesAgent/
├── frontend/      Vue 3 + TypeScript + Vite 前端
├── backend/       FastAPI AI Core、SQLite 与本地模型运行管理
├── docs/          架构、契约、开发说明、协作规范与问题复盘
└── server sync/   云同步服务预留目录，当前未实现
```

## 当前能力

- 工作区：打开一个后端配置的真实 Vault，编辑 Markdown，管理文件与目录。
- 检索与问答：FTS5、sqlite-vec、RRF 与轻量词面精排；搜索历史持久化到后端 SQLite；AI 对话自动检索知识库并返回 Citation。
- Agent 与扩展：持久化 Trace、可恢复 SSE、Tool/Permission、Skill、Plugin Command/Settings/Secret、隔离 Plugin Host。
- MCP：独立配置 stdio、Streamable HTTP 和旧 SSE Server，发现并调用工具；生产 stdio 沙箱等待 Tauri Host。
- 模型服务：OpenAI Chat/Compatible、OpenAI Responses、Anthropic Messages、Ollama；国内常用提供商 logo 预设、独立凭据、模型发现和自定义请求 JSON。
- 多模态：API 优先，未配置或响应无效时回退本地；`local_only` 禁止远程调用。任务、修订、事件、来源和回退原因写入 SQLite。
- 模型运行：默认 CPU，可选 CUDA 12.8 组件；固定模型 revision，按需启动独立子进程，交互检索优先排队，CUDA 初始化或显存失败时用同一冻结配置在 CPU 重试一次。
- 可观测性：输入、输出、缓存命中、推理 Token 与音频用量卡片；本地运行诊断保留最近 200 条，不保存正文、文件路径、密钥或异常全文。
- 界面偏好：设置页可即时切换全局中文/英文界面，并控制由系统词典提供的编辑器拼写检查；偏好目前保存于 Web 端设备配置，后续由 Tauri 配置存储接管。

## 本地模型

| 能力 | 当前模型 | 许可 | 说明 |
| --- | --- | --- | --- |
| 默认 Embedding | `hotchpotch/bekko-embedding-v1-a8m` | MIT | 384 维，中文检索默认选择 |
| 可选 Embedding | `ibm-granite/granite-embedding-97m-multilingual-r2` | Apache-2.0 | 384 维，多语言备选 |
| 音频转写与语言识别 | `Qwen/Qwen3-ASR-0.6B` | Apache-2.0 | 返回片段级时间边界 |
| 声纹提取与匹配 | `iic/speech_eres2netv2_sv_zh-cn_16k-common` | Apache-2.0 | 192 维声纹，供相似度和片段聚类使用 |

模型权重按代码中的固定 revision 下载并校验，推理阶段离线读取。当前说话人处理是能量分段、ASR 片段与 ERes2NetV2 聚类，不包含逐字强制对齐、同段多人或重叠语音分离。`HashEmbeddingProvider` 只用于确定性测试注入。

## 开发环境

| 环境 | 要求 |
| --- | --- |
| Git | 较新稳定版 |
| Node.js | 22+，推荐 24 |
| pnpm | 10+ |
| Python | 3.11+，推荐 3.12 |
| uv | 较新稳定版 |

当前 Web 联调不需要 Rust 和 Tauri。桌面端集成时再安装 Rust Toolchain 与 Tauri CLI。

## 初始化与启动

安装 API 与前端依赖：

```powershell
cd backend
uv sync
cd ../frontend
pnpm install
cd ..
```

在两个终端分别启动：

```powershell
# 终端一
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# 终端二
cd frontend
pnpm dev
```

前端地址为 <http://127.0.0.1:5173>，Vite 将 `/api` 和 `/health` 代理到 <http://127.0.0.1:8000>。后端提供健康检查 `/health`、服务状态 `/api/status`、API 文档 `/docs` 和机器可读契约 `/openapi.json`。

## 安装本地模型运行组件

API 环境保留在 `backend/.venv`，模型依赖安装到独立环境。默认安装 CPU：

```powershell
./backend/scripts/install-model-runtime.ps1
```

CUDA 为 Windows 可选组件，可在“设置 → 模型提供商 → 本地模型”中安装，也可保留 CPU 环境并创建独立 CUDA 环境：

```powershell
./backend/scripts/install-model-runtime.ps1 -Device cuda -RuntimeDirectory ./backend/.venv-models-cuda
$env:APP_MODEL_PYTHON = (Resolve-Path ./backend/.venv-models-cuda/Scripts/python.exe).Path
```

脚本固定 `torch`/`torchaudio` 2.9.1，CPU 使用官方 CPU wheel，CUDA 使用 cu128 wheel；脚本不会安装或修改 NVIDIA 驱动。模型权重需要在设置页显式下载，不会在推理时自动下载。

## 模型提供商与凭据

在“设置 → 模型提供商”中选择预设或创建自定义提供商。API Key 只在前端提交期间存在，不写入 Pinia 或 `localStorage`；后端将密文和开发主密钥保存到已忽略的 `backend/data/credentials/`，Provider 配置只保存 Credential ID。

无界面环境可使用 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或 `AINOTE_CREDENTIAL_<ID>`。当前 Fernet 存储用于 Web 联调，桌面端将沿用 Credential API 边界迁移到 Stronghold。

## 测试与构建

```powershell
cd backend
uv run pytest

cd ../frontend
pnpm test
pnpm build
```

当前回归基线为后端 559 项、前端 106 项测试通过，TypeScript 类型检查与生产构建通过。存在一条既有 Starlette/httpx 弃用提示和 Vite 大 bundle 提示；测试数量以当前分支实际输出和 CI 为准。

## 文档

| 文档 | 用途 |
| --- | --- |
| [文档总索引](docs/README.md) | 全部架构、契约、开发说明和复盘入口 |
| [前端 README](frontend/README.md) | 前端结构、运行方式和数据边界 |
| [后端 README](backend/README.md) | API Core、模型运行与配置 |
| [技术栈说明](docs/architecture/AI笔记软件技术栈说明-团队版-v2.3.md) | 当前技术基线、目标桌面架构与模块边界 |
| [多模态与模型运行](docs/development/多模态管线与模型运行开发说明.md) | 模型 revision、CPU/CUDA、路由、用量和接口 |
| [阶段 F 收尾验收](docs/development/阶段F收尾验收记录.md) | 自动化、CPU/CUDA 真实闭环和未关闭专项 |
| [后端接口契约](docs/contracts/后端接口契约-开发版.md) | 当前 HTTP/SSE 接口说明 |
| [第二阶段接口契约](docs/contracts/第二阶段接口契约-开发版.md) | 第二阶段公共 DTO 与行为边界 |

## 开发约定

- 后端依赖统一修改 `backend/pyproject.toml` 并执行 `uv sync`；模型依赖由 `backend/scripts/model-requirements.lock` 锁定。
- 前端依赖统一使用 pnpm，不混用 npm 或 yarn。
- `backend/.venv*`、模型权重、`frontend/node_modules` 和 `frontend/dist` 都是本地产物，不提交 Git。
- 前端不直接访问 SQLite 或厂商模型协议；持久数据通过 FastAPI 服务读写。
- 接口或数据结构变化时，同一提交同步更新前后端类型、契约和开发说明。
- 当前行为以代码、测试和运行中的 `/openapi.json` 为准；规划能力必须在文档中明确标注。
