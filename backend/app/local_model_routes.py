import asyncio
from fastapi import APIRouter
from app.services import model_diagnostics
from app.local_models import manager
from app.local_models.runtime import RuntimeConfig, configuration, configure, interpreter, runtime

router = APIRouter(prefix="/api/local-models", tags=["Local models"])


@router.get("/runtime-components/cuda")
async def cuda_status():
    from app.local_models import components
    return await components.status()


@router.post("/runtime-components/cuda", status_code=202)
async def install_cuda():
    from app.local_models import components
    return await components.install()


@router.get("")
async def list_models():
    items, diagnostics = await asyncio.gather(asyncio.to_thread(manager.describe), asyncio.to_thread(model_diagnostics.recent))
    return {**items, "runtime_installed": interpreter().is_file(), "config": configuration(),
            "active_models": list(runtime.active.values()), "queued_requests": len(runtime.waiters),
            "last_inference": diagnostics[-1] if diagnostics else None}


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
    return {"items": await asyncio.to_thread(model_diagnostics.recent), "config": configuration(), "scope": "application_last_200_attempts",
            "contains": "model_revision_device_timing_resources_only"}
