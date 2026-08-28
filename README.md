# Notes Agent（暂命名） 团队开发说明

> 本文件用于团队开发期间快速配置环境和启动项目，不是正式的项目 README。

## 当前目录

```text
NotesAgent/
├── frontend/      Vue 3 + TypeScript + Vite 前端
├── backend/       FastAPI + Pydantic 后端
├── docs/          分工与技术栈说明
└── server sync/   云同步服务预留目录，当前未实现
```

## 开发环境

当前前后端壳子需要：

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

当前壳子暂不需要 Rust 和 Tauri。开始桌面端集成后，再按照 `docs/AI笔记软件技术栈说明-团队版-v2.2.md` 安装 Rust Toolchain 与 Tauri CLI。

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

构建产物位于 `frontend/dist`，该目录不提交到 Git。

## 日常开发注意事项

- Python 依赖统一修改 `backend/pyproject.toml`，修改后执行 `uv sync`。
- 前端依赖统一使用 pnpm 安装，不要混用 npm 或 yarn。
- `backend/.venv`、`frontend/node_modules`、`frontend/dist` 均为本地生成目录，不提交到 Git。
- API 默认监听 `127.0.0.1:8000`，前端默认监听 `127.0.0.1:5173`。
- 后端附件目录默认是 `backend/data/attachments`，可通过 `APP_ATTACHMENTS_PATH` 覆盖；该目录由桌面 Host 管理。
- 跨模块接口发生变化时，需要同步更新前后端类型和 `docs` 中的接口说明。
- 当前前后端接口清单见 `docs/后端接口契约-开发版.md`，OpenAPI 以 `/openapi.json` 为准。
- 前端页面、交互、状态管理和第一阶段验收要求见 `docs/前端页面需求说明-开发版.md`。
- 分支、提交、Pull Request、Review 和冲突处理规范见 `docs/Git使用细则-团队开发版.md`。
