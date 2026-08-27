from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.schemas import HealthResponse, ServiceStatusResponse

settings = get_settings()

app = FastAPI(
    title=settings.name,
    version=settings.version,
    description="AI 笔记软件的本地 FastAPI 服务壳子。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
