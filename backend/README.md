# Notes Agent Backend

FastAPI + Pydantic 的本地 AI Core / Agent Core。项目使用 uv 管理依赖和虚拟环境。

当前实现包含 Knowledge/Retrieval、Chat、Agent Runtime、Tool/Permission、Skill/Plugin、Provider Adapter、任务、索引和开发阶段凭据加密存储。Provider 支持 Mock、OpenAI Chat/OpenAI-Compatible 与 Ollama；OpenAI Responses、Anthropic Messages、MCP 独立 Host 和真实语音模型仍属于后续阶段。

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

当前基线为 81 项测试通过。Provider API Key 可通过前端设置页写入，也可用 `OPENAI_API_KEY`、`DEEPSEEK_API_KEY` 或 `AINOTE_CREDENTIAL_<ID>` 注入；不要把真实密钥写入仓库。

团队接口清单见 `../docs/contracts/后端接口契约-开发版.md`，机器可读契约以运行时的 `/openapi.json` 为准。

AI Core 与 Agent Core 的模块边界、Mock Provider 和 Tool Calling 调试方式见 `../docs/development/AI-Core与Agent-Core开发说明.md`。

Knowledge Core 与 Retrieval Core 的模块边界、数据模型、接口与检索流程见 `../docs/development/Knowledge与Retrieval-Core开发说明.md`。
