from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ServiceStatusResponse(HealthResponse):
    name: str
    version: str
    environment: str
