import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """应用基础配置；正式环境可通过 APP_* 环境变量覆盖。"""

    name: str
    version: str
    environment: str
    host: str
    port: int


@lru_cache
def get_settings() -> Settings:
    return Settings(
        name=os.getenv("APP_NAME", "Notes Agent AI Core"),
        version=os.getenv("APP_VERSION", "0.1.0"),
        environment=os.getenv("APP_ENVIRONMENT", "development"),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
    )
