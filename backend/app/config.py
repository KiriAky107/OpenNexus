import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

# backend 目录（本文件位于 backend/app/config.py，父目录的父目录即 backend）
BACKEND_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    """应用基础配置；正式环境可通过 APP_* 环境变量覆盖。

    数据目录默认落在 backend/data 下：app.db 保存 SQLite 索引，
    vault/ 保存 Markdown 笔记。三者均可通过环境变量覆盖，便于测试与正式部署分离。
    """

    name: str
    version: str
    environment: str
    host: str
    port: int
    data_dir: Path
    db_path: Path
    vault_path: Path
    attachments_path: Path
    benchmark_datasets_path: Path
    exports_path: Path


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(os.getenv("APP_DATA_DIR", str(BACKEND_DIR / "data")))
    return Settings(
        name=os.getenv("APP_NAME", "OpenNexus AI Core"),
        version=os.getenv("APP_VERSION", "0.5.4-alpha"),
        environment=os.getenv("APP_ENVIRONMENT", "development"),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        data_dir=data_dir,
        db_path=Path(os.getenv("APP_DB_PATH", str(data_dir / "app.db"))),
        vault_path=Path(os.getenv("APP_VAULT_PATH", str(data_dir / "vault"))),
        attachments_path=Path(
            os.getenv("APP_ATTACHMENTS_PATH", str(data_dir / "attachments"))
        ),
        benchmark_datasets_path=Path(
            os.getenv("APP_BENCHMARK_DATASETS_PATH", str(data_dir / "benchmarks"))
        ),
        exports_path=Path(os.getenv("APP_EXPORTS_PATH", str(data_dir / "exports"))),
    )
