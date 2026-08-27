# Backend

FastAPI + Pydantic 的最小后端壳子。项目使用 uv 管理依赖和虚拟环境。

```powershell
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`uv sync` 首次运行时会自动创建由 uv 管理的 `.venv`，无需手动执行 `python -m venv` 或激活环境。

启动后可访问：

- 健康检查：<http://127.0.0.1:8000/health>
- API 文档：<http://127.0.0.1:8000/docs>
