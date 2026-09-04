from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHttpException

from app.config import get_settings
from app.container import container
from app.errors import ApiError, api_error_handler, http_error_handler, validation_error_handler
from app.export import service as export_service
from app.routes import router as api_router
from app.schemas import HealthResponse, ServiceStatusResponse

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 重启后内存注册表为空，清理上一次运行遗留的导出产物，避免磁盘垃圾堆积。
    export_service.cleanup_orphan_files()
    yield
    # 第三方 MCP Server 必须跟随 AI Core 退出，不能遗留孤儿进程。
    container.plugins.shutdown()
    container.mcp_servers.shutdown()


app = FastAPI(
    title=settings.name,
    version=settings.version,
    description="AI 笔记软件的本地 AI Core 与 Agent Core 服务。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHttpException, http_error_handler)
app.include_router(api_router)


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/api/status", response_model=ServiceStatusResponse, tags=["System"])
async def service_status() -> ServiceStatusResponse:
    return ServiceStatusResponse(
        name=settings.name,
        version=settings.version,
        environment=settings.environment,
    )
