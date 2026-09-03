# Notes Agent（暂命名） 团队开发说明

> 本文件用于团队开发期间快速配置环境和启动项目，不是正式的项目 README。

> 当前基线：2026-08-30。第一阶段 Web 联调版的前端页面、Knowledge/Retrieval Core、AI/Agent Core、Extension Core、Provider 预设与本地加密凭据链路均已实现；Tauri Host、Stronghold、真实桌面文件系统和 Sync Server 尚未接入。

## 当前目录

```text
NotesAgent/
├── frontend/      Vue 3 + TypeScript + Vite 前端
├── backend/       FastAPI + Pydantic 后端
├── docs/          架构、契约、开发说明、协作规范与问题复盘
└── server sync/   云同步服务预留目录，当前未实现
```

## 开发环境

当前开发版需要：

| 环境 | 要求 | 说明 |
| --- | --- | --- |
| Git | 较新稳定版 | 代码版本管理 |
| Node.js | 22 或更高版本 | 推荐使用 Node.js 24 |
| pnpm | 10 或更高版本 | 前端依赖与脚本管理 |
| Python | 3.11 或更高版本 | 推荐使用 Python 3.12 |
| uv | 较新稳定版 | 后端依赖和虚拟环境管理 |

检查本机环境：

```powershell
git --version
node --version
pnpm --version
python --version
uv --version
```

当前 Web 联调不需要 Rust 和 Tauri。开始桌面端集成后，再按照 `docs/architecture/AI笔记软件技术栈说明-团队版-v2.3.md` 安装 Rust Toolchain 与 Tauri CLI。

## 首次初始化

### 后端

```powershell
cd backend
uv sync
cd ..
```

`uv sync` 会根据 `backend/pyproject.toml` 安装依赖，并自动创建和管理 `backend/.venv`，不需要手动创建或激活虚拟环境。

### 前端

```powershell
cd frontend
pnpm install
cd ..
```

## 启动开发环境

前端和后端需要在两个终端中分别启动。

### 终端一：启动后端

```powershell
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

后端地址：

- 健康检查：<http://127.0.0.1:8000/health>
- 服务状态：<http://127.0.0.1:8000/api/status>
- API 文档：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>

#### 开发环境使用外部模型

在“设置 → 模型提供商”中选择 DeepSeek 或 OpenAI 预设后，直接在密码输入框填写 API Key。前端只在提交期间持有该值，不写入 Pinia 或 localStorage；AI Core 将其加密保存到本机 `backend/data/credentials/`，Provider 配置只保留内部 Credential ID。

该目录同时包含本地开发用主密钥和密文，并已加入 `.gitignore`。这提供本地静态加密和完整性校验，但不能替代操作系统凭据库。开始 Tauri 桌面集成后，应将存储实现迁移到 Stronghold，保留现有 Credential API 与 Provider 接口边界。

无界面或自动化环境仍可使用 `DEEPSEEK_API_KEY`、`OPENAI_API_KEY` 或 `AINOTE_CREDENTIAL_<ID>` 注入；设置页保存的本地密钥优先，环境变量仅在本地未保存对应 Credential ID 时作为回退。密钥不得写入仓库文件、README、Issue、提交信息或聊天记录。

### 终端二：启动前端

```powershell
cd frontend
pnpm dev
```

前端地址：<http://127.0.0.1:5173>

开发环境中，Vite 会将 `/api` 和 `/health` 请求代理到 `http://127.0.0.1:8000`。联调时应先启动后端，再启动或刷新前端。

## 测试与构建

后端测试：

```powershell
cd backend
uv run pytest
```

前端类型检查及生产构建：

```powershell
cd frontend
pnpm build
```

前端单元与组件测试：

```powershell
cd frontend
pnpm test
```

当前回归基线为后端 157 项测试、前端 29 项测试，且生产构建通过。测试数量会随功能增长，以本地实际输出和 CI 为准。

构建产物位于 `frontend/dist`，该目录不提交到 Git。

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [文档总索引](docs/README.md) | 文档分类、阅读顺序和维护规则 |
| [技术栈说明](docs/architecture/AI笔记软件技术栈说明-团队版-v2.3.md) | 目标架构、第二阶段技术边界与模块依赖 |
| [第二阶段分工表](docs/architecture/第二阶段团队分工表.md) | 第二阶段人员职责、任务顺序、协作关系与验收项 |
| [后端接口契约](docs/contracts/后端接口契约-开发版.md) | HTTP/SSE 接口、错误和当前实现状态 |
| [第二阶段接口契约](docs/contracts/第二阶段接口契约-开发版.md) | 第二阶段公共 DTO、计划接口、SSE、错误码与联调顺序 |
| [AI Core 与 Agent Core](docs/development/AI-Core与Agent-Core开发说明.md) | Provider、Agent、Tool、Permission 与 Extension Core |
| [MCP Bridge 与 Plugin Host](docs/development/MCP-Bridge与Plugin-Host开发说明.md) | stdio MCP、隔离进程、Tool 映射、状态与错误边界 |
| [Git 使用细则](docs/guides/Git使用细则-团队开发版.md) | 分支、提交、PR、Review 与合并流程 |
| [CI/CD 细则](docs/guides/CI-CD细则-团队开发版.md) | Gitea 流水线、质量门禁、产物、发布与回滚规则 |
| [Agent Trace 复盘](docs/retrospectives/Agent-Core第二阶段问题与修复复盘.md) | Agent 持久化、SSE 恢复、事件契约与脱敏问题复盘 |

## 日常开发注意事项

- Python 依赖统一修改 `backend/pyproject.toml`，修改后执行 `uv sync`。
- 前端依赖统一使用 pnpm 安装，不要混用 npm 或 yarn。
- `backend/.venv`、`frontend/node_modules`、`frontend/dist` 均为本地生成目录，不提交到 Git。
- API 默认监听 `127.0.0.1:8000`，前端默认监听 `127.0.0.1:5173`。
- 后端附件目录默认是 `backend/data/attachments`，可通过 `APP_ATTACHMENTS_PATH` 覆盖；该目录由桌面 Host 管理。
- 跨模块接口发生变化时，需要同步更新前后端类型和 `docs` 中的接口说明。
- 当前已实现接口见 `docs/contracts/后端接口契约-开发版.md`，第二阶段规划接口见 `docs/contracts/第二阶段接口契约-开发版.md`；已实现能力以 `/openapi.json` 为准。
- 前端页面、交互、状态管理和第一阶段验收要求见 `docs/contracts/前端页面需求说明-开发版.md`。
- 分支、提交、Pull Request、Review 和冲突处理规范见 `docs/guides/Git使用细则-团队开发版.md`。
- CI 检查、产物、发布和回滚规范见 `docs/guides/CI-CD细则-团队开发版.md`。
