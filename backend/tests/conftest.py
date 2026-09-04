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
    # Unit tests explicitly inject deterministic embeddings. Production uses real models.
    from app import container as container_module
    from app.services import note_service
    from app.retrieval.engine import engine
    from app.retrieval.embedding import HashEmbeddingProvider
    from app.providers.routing import ModelRoutingService
    def test_routing(providers, credentials):
        return ModelRoutingService(providers, credentials, local_embedding=HashEmbeddingProvider())
    monkeypatch.setattr(container_module, "_local_model_routing", test_routing)
    monkeypatch.setattr(container_module.container.model_routing, "local_embedding", HashEmbeddingProvider())
    monkeypatch.setattr(note_service, "embedding", HashEmbeddingProvider())
    test_embedding = HashEmbeddingProvider()
    monkeypatch.setattr(engine, "embedding", test_embedding)
    monkeypatch.setattr(engine, "_routed_defaults", (test_embedding, engine.vector_store))
    yield
    get_settings.cache_clear()
