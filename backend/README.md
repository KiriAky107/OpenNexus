# Backend

FastAPI + Pydantic 的本地 AI Core / Agent Core。项目使用 uv 管理依赖和虚拟环境。

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`uv sync` 首次运行时会自动创建由 uv 管理的 `.venv`，无需手动执行 `python -m venv` 或激活环境。

启动后可访问：

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>

团队接口清单见 `../docs/后端接口契约-开发版.md`，机器可读契约以运行时的 `/openapi.json` 为准。

AI Core 与 Agent Core 的模块边界、Mock Provider 和 Tool Calling 调试方式见 `../docs/AI-Core与Agent-Core开发说明.md`。
