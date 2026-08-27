from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHttpException

from app.contracts import ErrorDetail, ErrorResponse


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details)
    )
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body))


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": exc.errors()},
        )
    )
    return JSONResponse(status_code=422, content=jsonable_encoder(body))


async def http_error_handler(_: Request, exc: StarletteHttpException) -> JSONResponse:
    code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
    body = ErrorResponse(
        error=ErrorDetail(code=code, message=str(exc.detail), details={})
    )
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(body))


def not_implemented(resource: str) -> None:
    raise ApiError(
        status_code=501,
        code="NOT_IMPLEMENTED",
        message=f"{resource} contract is available, but its business service is not implemented.",
        details={"resource": resource},
    )
