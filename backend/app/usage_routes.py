from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from app.errors import ApiError
from app.services.usage_service import aggregate

router = APIRouter(prefix="/api/usage", tags=["Usage"])


@router.get("")
async def usage(start: datetime | None = None, end: datetime | None = None,
                provider_id: str | None = Query(None, max_length=200), model: str | None = Query(None, max_length=200),
                source: str | None = None):
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=7)
    if not start.tzinfo or not end.tzinfo or end <= start:
        raise ApiError(422, "INVALID_TIME_RANGE", "Provide timezone-aware start/end with end after start.")
    if source not in {None, "local", "api"}:
        raise ApiError(422, "INVALID_USAGE_SOURCE", "Unknown usage source.")
    return aggregate(start, end, provider_id, model, source)
