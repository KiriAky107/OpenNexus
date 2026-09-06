from fastapi import APIRouter, Query
from app.operation_logs import get_store

router = APIRouter(prefix='/api/logs', tags=['Diagnostics'])


@router.get('')
def list_logs(limit: int = Query(50, ge=1, le=200), before: int | None = Query(None, ge=1),
              level: str = Query('', pattern='^(|INFO|WARNING|ERROR|CRITICAL)$'),
              source: str = Query('', max_length=100), q: str = Query('', max_length=200)):
    return get_store().query(limit=limit, before=before, level=level, source=source, q=q)
