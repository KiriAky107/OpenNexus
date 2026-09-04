# Notes Agent Backend

FastAPI + Pydantic 的本地 AI Core / Agent Core。项目使用 uv 管理依赖和虚拟环境。

当前实现包含 Knowledge/Retrieval、Chat、Agent、Tool/Permission、Skill/Plugin、MCP、模型提供商与多模态任务。支持 OpenAI Chat/Compatible、Responses、Anthropic Messages 和 Ollama；真实本地 Embedding、ASR、声纹模型默认 CPU，CUDA 显式选装。操作系统级 Plugin 沙箱仍属于后续阶段。

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`uv sync` 首次运行时会自动创建由 uv 管理的 `.venv`，无需手动执行 `python -m venv` 或激活环境。

启动后可访问：

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>
- OpenAPI：<http://127.0.0.1:8000/openapi.json>

运行回归测试：

```powershell
uv run pytest
```

阶段 F 后端基线为 472 项测试通过。Provider API Key 可通过前端设置页写入，也可用 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或 `AINOTE_CREDENTIAL_<ID>` 注入；不要把真实密钥写入仓库。`plugin.*` 是 Plugin Settings 的保留凭据命名空间，通用 Provider 凭据接口不能读写。

本地模型 CPU/CUDA 安装、多模态任务、Token 用量与自定义 JSON 见 [多模态管线与模型运行开发说明](../docs/development/多模态管线与模型运行开发说明.md)。

团队接口清单见 `../docs/contracts/后端接口契约-开发版.md`，机器可读契约以运行时的 `/openapi.json` 为准。

AI Core 与 Agent Core 的模块边界、Mock Provider 和 Tool Calling 调试方式见 `../docs/development/AI-Core与Agent-Core开发说明.md`。

Knowledge Core 与 Retrieval Core 的模块边界、数据模型、接口与检索流程见 `../docs/development/Knowledge与Retrieval-Core开发说明.md`。
