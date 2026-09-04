from fastapi import APIRouter
from app.local_models import manager
from app.local_models.runtime import RuntimeConfig, configuration, configure, interpreter, runtime

router = APIRouter(prefix="/api/local-models", tags=["Local models"])


@router.get("")
async def list_models():
    return {**manager.describe(), "runtime_installed": interpreter().is_file(), "config": configuration(),
            "active_models": list(runtime.active.values()), "queued_requests": len(runtime.waiters),
            "last_inference": runtime.diagnostics[-1] if runtime.diagnostics else None}


@router.put("/config")
async def update_config(request: RuntimeConfig):
    return configure(request)


@router.post("/{key}/download", status_code=202)
async def download(key: str):
    return await manager.download(key)


@router.post("/{key}/cancel")
async def cancel(key: str):
    return await manager.cancel_download(key)


@router.delete("/{key}")
async def delete(key: str):
    return await manager.delete(key)


@router.get("/diagnostics")
async def diagnostics():
    return {"items": runtime.diagnostics, "config": configuration(), "scope": "current_process",
            "contains": "model_revision_device_timing_resources_only"}
