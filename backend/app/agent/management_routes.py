"""Trusted UI endpoints; model tools can propose, but cannot approve mutations."""
from fastapi import APIRouter, Query
from app.agent.management import (DefinitionConfig, DefinitionWrite, StartRequest, ReviewRequest,
    CollaborationPlan, create_definition, update_definition, store)
from app.agent.collaboration import coordinator
from app.contracts import BudgetDecisionRequest
from app.errors import ApiError

router = APIRouter(prefix='/agent', tags=['Agent'])


def runtime():
    from app.container import container
    return container.agent


@router.get('/definitions')
async def definitions():
    return {'items': store.list('definition')}


@router.post('/definitions')
async def create(config: DefinitionConfig):
    return create_definition(config, runtime())


@router.put('/definitions/{identifier}')
async def update(identifier: str, request: DefinitionWrite):
    return update_definition(identifier, request, runtime())


@router.delete('/definitions/{identifier}')
async def delete(identifier: str, expected_revision: int = Query(ge=1)):
    store.delete(identifier, expected_revision)
    return {'status': 'completed'}


@router.post('/definitions/{identifier}/runs')
async def start(identifier: str, request: StartRequest):
    return await coordinator(runtime()).start(identifier, request)


@router.get('/collaborations')
async def collaborations():
    manager = coordinator(runtime())
    return {'items': [manager.get(item['id']) for item in store.list('collaboration')]}


@router.post('/collaborations')
async def plan(request: CollaborationPlan):
    return coordinator(runtime()).plan(request)


@router.get('/collaborations/{identifier}')
async def get_group(identifier: str):
    return coordinator(runtime()).get(identifier)


@router.post('/collaborations/{identifier}/review')
async def review_group(identifier: str, request: ReviewRequest):
    manager = coordinator(runtime())
    if request.decision == 'reject':
        return await manager.cancel(identifier)
    return await manager.approve(identifier, request.expected_revision)


@router.post('/collaborations/{identifier}/cancel')
async def cancel_group(identifier: str, member_id: str | None = None):
    return await coordinator(runtime()).cancel(identifier, member_id)


@router.post('/collaborations/{identifier}/budget/{request_id}')
async def extend_group(identifier: str, request_id: str, request: BudgetDecisionRequest):
    return await coordinator(runtime()).extend_budget(identifier, request_id, request.additional_tokens)


@router.get('/changes/{identifier}')
async def get_change(identifier: str):
    return store.get('change', identifier)


@router.post('/changes/{identifier}/review')
async def review_change(identifier: str, request: ReviewRequest):
    # This route is deliberately absent from the model tool catalog.
    manager = coordinator(runtime())
    async with manager.lock:
        return store.review_change(identifier, request, runtime())
