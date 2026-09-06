from contextlib import asynccontextmanager
import asyncio
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHttpException

from app.config import get_settings
from app.container import container
from app.errors import ApiError, api_error_handler, http_error_handler, validation_error_handler
from app.routes import router as api_router
from app.media_routes import router as media_router
from app.local_model_routes import router as local_model_router
from app.usage_routes import router as usage_router
from app.provider_preview_routes import router as provider_preview_router
from app.schemas import HealthResponse, ServiceStatusResponse
from app.log_routes import router as log_router
from app.operation_logs import install_logging, log_event, request_id, shutdown_logging

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    install_logging()
    log_event('system', 'service.started')
    from app.services import transcription_service
    transcription_service.recover_interrupted()
    try:
        yield
    finally:
        await container.agent.shutdown()
        from app.services import index_service
        await index_service.shutdown()
        await transcription_service.shutdown()
        from app.local_models import components
        await components.shutdown()
        from app.local_models import manager
        for _, key in list(manager._downloads):
            await manager.cancel_download(key)
        container.plugins.shutdown()
        container.mcp_servers.shutdown()
        log_event('system', 'service.stopped')
        await asyncio.to_thread(shutdown_logging)


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
app.include_router(media_router)
app.include_router(local_model_router)
app.include_router(usage_router)
app.include_router(provider_preview_router)
app.include_router(log_router)


@app.middleware('http')
async def operation_log(request, call_next):
    token = request_id.set(uuid4().hex)
    started = perf_counter()
    status = 500
    failure = None
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers['X-Request-ID'] = request_id.get()
        return response
    except Exception as exc:
        failure = exc
        raise
    finally:
        # Do not record query strings, request/response bodies or arbitrary URLs.
        route = getattr(request.scope.get('route'), 'path', 'unmatched')
        if not route.startswith('/api/logs') and (request.method not in {'GET', 'HEAD', 'OPTIONS'} or status >= 400 or perf_counter() - started > 1):
            log_event('http', 'request.finished', level='ERROR' if status >= 500 else 'WARNING' if status >= 400 else 'INFO',
                      error=failure, method=request.method, route=route, status=status,
                      duration_ms=round((perf_counter() - started) * 1000, 2),
                      **{k: v for k, v in request.path_params.items() if k in {'run_id', 'task_id', 'note_id', 'job_id', 'provider_id'}})
        request_id.reset(token)


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
