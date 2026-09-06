# Notes Agent（暂命名） 团队开发说明

> 第二阶段收尾（开发分支，2026-09-07）：标准 Agent/RAG Benchmark 与报告页、函数图预览、三格式快照导出及真实 Provider/MCP 结果见[实现与验收记录](docs/development/第二阶段收尾实现与验收-2026-09-07.md)。当前分支尚未合并，不更改下文历史 main 基线。

> 本文件用于团队开发期间快速配置环境、启动项目并了解当前实现状态，不是正式的项目 README。

NotesAgent 是本地优先的 AI 笔记与知识库项目。当前可运行形态为 Vue/Vite Web 前端与 FastAPI AI Core：Markdown 和附件保存在本地 Vault，SQLite 管理元数据、全文索引、向量空间、搜索历史、AI 会话、任务、Agent Trace、多模态任务及运行诊断。AI 对话已接入知识库检索，会话与消息由后端持久化并供 Web 和桌面客户端共用。

截至 2026-09-06，第一阶段及第二阶段 A～F 的工程范围已经合并到 `main`。当前已完成真实 Workspace、混合检索与知识库问答、Agent/Tool/Permission、Skill/Plugin、MCP 配置与调用、模型提供商与路由、RAG Benchmark，以及本地 Embedding、音频转写和片段级声纹聚类。Tauri/Rust Host、Stronghold、原生多 Vault 文件系统、生产级 MCP 沙箱和 Sync Server 尚未接入。

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
- 运行日志：统一查看向量/模型错误、Agent、任务与 HTTP 操作；独立后台存储最近 20,000 条，支持错误码/关联 ID 筛选和游标分页。入口无需打开 Vault，详见 [后台运行日志与压力问题修复](docs/development/后台运行日志与压力问题修复.md)。
- 界面偏好：设置页可即时切换全局中文/英文界面，并控制由系统词典提供的编辑器拼写检查；偏好目前保存于 Web 端设备配置，后续由 Tauri 配置存储接管。

## 第二阶段最新合并（2026-09-06）

PR #31 已合并。工作区打开与 HTTP 保存不再等待向量推理；正文和全文索引先可用，向量随后后台更新。“已保存”与“向量就绪”是两个独立状态。Skill / Plugin 支持 ZIP 安装与本地安装状态恢复，并已提供功能示例包；远程社区仍是第三阶段计划。

新增开发说明：

- [工作区后台索引与保存](docs/development/工作区后台索引与保存开发说明.md)：状态、并发、恢复和验证。
- [模型隔离向量索引与增量登记](docs/development/模型隔离向量索引与增量登记.md)：持久化 sqlite-vec 空间、旧向量复用、外部新增文件增量计算与检索性能验证。
- [Mermaid 预览与缩放](docs/development/Mermaid预览与缩放开发说明.md)：大图适配、鼠标缩放和文字裁切修复。
- [扩展安装持久化与社区包](docs/development/扩展安装持久化与社区包开发说明.md)：安装边界和示例包验证。
- [模型上下文管理](docs/development/模型上下文管理.md)：全局人设、预算估算和摘要限制。
- [第三阶段实施规划](docs/architecture/第三阶段实施规划.md)：Tauri Rust 容器、各社区与 Sync Server。

代码基线 `a5c44c4` 的验证结果为后端 621 项、前端 345 项测试通过，前端生产构建通过。这是该提交的回归记录，不表示全部真实厂商及设备场景完成专项验收。

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
uv run python scripts/dev-server.py

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

## 主题包与仓库发布（临时规范）

主题页支持本地文件及 HTTP(S) 文件直链导入。两种入口均先解析、校验并展示清单和 CSS，用户点击安装后才写入本地存储。安装不会自动启用主题。

### 单文件

使用 UTF-8 编码，扩展名 `.theme`、`.yaml` 或 `.yml`。内容为 YAML 清单、一行 `---`、完整 CSS。可参考 `frontend/src/assets/themes/paper-moments.theme`。

### ZIP

一个 ZIP 只包含一个主题。清单命名为 `theme.yaml`、`theme.yml`、`manifest.yaml` 或 `manifest.yml`，可以放在顶层，也可以放在仓库压缩包的子目录中。

```text
my-theme/
  theme.yaml
  styles/
    theme.css
```

```yaml
theme_id: my-theme
name: My Theme
version: 1.0.0
author: your-name
min_app_version: 0.2.0
is_dark: false
css_entry: styles/theme.css
```

`css_entry` 相对于清单目录解析，不允许绝对路径、反斜杠及 `..`。CSS 应以 `[data-theme="my-theme"]` 限定主题样式。也支持仅包含一个 `.theme` 文件的 ZIP。

目前安装持久化的是清单和 CSS，不会托管 ZIP 内的图片、字体等资源；需要这些资源时请将它们内嵌为 CSS data URL。禁止 `@import` 和脚本表达式。

### URL 与社区仓库

发布主题仓库时可提供原始 `.theme` 文件链接或 ZIP 发布附件直链，不要使用仓库 HTML 浏览页面地址。下载请求不携带 Cookie 或 HTTP 登录信息，服务器需允许应用来源的 CORS 请求；暂不支持私有仓库认证。

下载和本地文件限制为 5 MB；ZIP 解压总大小限制为 10 MB，最多 100 个条目。URL 下载超时为 30 秒。取消导入会取消下载，过期请求不会替换当前待安装主题。更新时递增清单版本号，并保持 `theme_id` 稳定。


### 主题兼容性与安装前预览

当前应用版本从 `frontend/package.json` 读取（0.2.0）。清单的 `version`、`min_app_version` 必须使用有效 SemVer；最低版本高于应用版本时，检查、安装和启用都会拒绝。文件、URL、ZIP 导入共用此规则。

导入检查通过后可点击“预览主题效果”。预览使用无脚本的 sandbox iframe，与当前应用样式和主题存储隔离；CSP 禁止远程资源，仅允许内联样式及 data 图片/字体。预览不等同于安装。


### 用量趋势与纸间时光 1.5

模型设置页将提供商、本地模型、用量统计分成独立卡片。用量趋势支持近 7 天、30 天、90 天及自定义时间，沿用提供商/模型/来源筛选；按本机 UTC 偏移分组（长区间自动合并到最多 90 组）。可切换输入、输出、总 Token 和请求次数，本地为芯片实色图例，提供商为连接斜纹图例。仅汇总已报告值，并提供覆盖数与可展开的数据表，缺失不补零。

纸间时光更新至 1.5.0，通用卡片、执行事件、引用、模型路由及弹窗统一使用纸张、虚线、胶带和叠纸阴影。已安装旧版本时，在主题社区点击“更新”应用新版样式。


## Skill / Plugin ZIP 安装（临时规范）

第三阶段完整规划见[桌面容器、扩展社区与多设备同步](docs/architecture/第三阶段实施规划.md)，包含 Tauri/Rust、各社区、Sync Server、迁移、建议分工和验收门禁；该文档是计划，不代表相关服务已经实现。

可运行的社区准备包见 [`backend/extensions/community/README.md`](backend/extensions/community/README.md)：包含 Markdown 检查 Plugin、配套笔记检查 Skill、可重复构建脚本和带 SHA-256 的包索引。

安装弹窗支持 ZIP 文件和 AI Core 主机上的本地目录。ZIP 根目录须包含 `skill.yaml` 或 `plugin.yaml`；也支持整个包放在唯一的顶层文件夹中。每个 ZIP 安装一个扩展，清单字段沿用现有 Skill / Plugin 契约。

```text
my-skill.zip                 my-plugin.zip
└─ my-skill/                 ├─ plugin.yaml
   ├─ skill.yaml            ├─ 后端入口及资源文件
   └─ prompt.md（可选）      └─ 其他包内资源
```

ZIP 最大 10 MiB，解压总大小最大 50 MiB，最多 2048 个条目；支持 stored/deflate。拒绝加密条目、符号链接、特殊文件、越界路径以及重复或大小写冲突路径。选择文件后点击安装才上传；后端解压并沿用现有清单、依赖及权限校验，不自动授予权限或启动 Plugin 进程。

解压文件保存在 AI Core 数据目录的 `extension-packages/` 下，安装失败会清理本次目录。此功能不改变扩展运行时现有的安装记录持久化机制；目前重启后仍需重新注册包。扩展 ZIP 暂不支持 URL 下载；主题 ZIP 使用其独立的导入规则。
