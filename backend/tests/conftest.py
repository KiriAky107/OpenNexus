"""pytest 全局隔离：把所有测试的数据目录/数据库/Vault 重定向到临时目录。

这样测试不会读写真实的 backend/data（真实索引与笔记），也使得「默认库应为空」这类
断言在任意本机状态下都确定成立——即使开发者已在本地跑过 rebuild。
"""

from __future__ import annotations

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(data_dir))
    monkeypatch.setenv("APP_DB_PATH", str(data_dir / "app.db"))
    monkeypatch.setenv("APP_VAULT_PATH", str(tmp_path / "vault"))
    # 清除 lru 缓存，让本次测试内的 get_settings() 读到临时目录
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
