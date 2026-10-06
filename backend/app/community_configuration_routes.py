import asyncio
import threading
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field

from app.container import container
from app.contracts import Contract
from app.errors import ApiError
from app.extensions.mcp_registry import McpRegistryError
from app.services import community_configuration as service

router = APIRouter(prefix='/api/community/configurations', tags=['Community configurations'])


class Preview(Contract):
    slot: str = Field(pattern=r'^[0-9a-f]{64}$')
    target: str = Field(min_length=1, max_length=100)


class Apply(Contract):
    review_id: UUID
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    operation_id: UUID


async def call(action, *args):
    try:
        return await asyncio.to_thread(action, *args, container.mcp_servers)
    except McpRegistryError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from None


@router.get('/targets')
async def targets(slot: str):
    return await call(service.targets, slot)


@router.post('/preview')
async def preview(request: Preview):
    return await call(service.preview, request.slot, request.target)


@router.post('/apply')
async def apply(application: Apply, request: Request):
    cancelled = threading.Event()
    def checkpoint():
        if cancelled.is_set():
            service.fail('REQUEST_CANCELLED')
    async def run():
        try:
            return await asyncio.to_thread(service.apply, str(application.review_id), application.fingerprint,
                                           str(application.operation_id), container.mcp_servers, checkpoint)
        except McpRegistryError as exc:
            raise ApiError(exc.status_code, exc.code, exc.message) from None
    worker = asyncio.create_task(run())
    try:
        while not worker.done():
            await asyncio.wait({worker}, timeout=0.1)
            if await request.is_disconnected():
                cancelled.set()
        return await worker
    finally:
        cancelled.set()
        if not worker.done():
            worker.add_done_callback(lambda task: None if task.cancelled() else task.exception())


@router.get('/operations/{operation_id}')
async def operation(operation_id: UUID, fingerprint: str):
    return await call(service.operation, str(operation_id), fingerprint)
