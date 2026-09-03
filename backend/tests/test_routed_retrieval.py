"""Phase E route integration: deterministic runtimes, isolated DBs, no network."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from app import repository
from app.config import get_settings
from app.contracts import IndexRebuildRequest, SearchMode, SearchRequest
from app.database.db import connect, transaction
from app.retrieval import routed_vectors
from app.retrieval.embedding import HashEmbeddingProvider
from app.retrieval.engine import RetrievalEngine, engine
from app.retrieval.reranker import LexicalReranker
from app.retrieval.vectorstore import SqliteVecStore, VectorHit
from app.services import index_service, note_service


@dataclass
class FakeRuntime:
    model_id: str = "space-a"
    dimensions: int = 3  # Deliberately differs from sqlite-vec's fixed 128.
    source: str = "api"
    error: BaseException | None = None
    calls: list[list[str]] = field(default_factory=list)
    result_override: object | None = None

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.error is not None:
            raise self.error
        if self.result_override is not None:
            return self.result_override
        vectors = []
        for text in texts:
            # The API associates "apple" with banana; hash retrieval picks apple.
            first = text == "apple orchard"
            if self.model_id == "space-b":
                first = not first
            vectors.append(([1.0, 0.0] if first else [0.0, 1.0]) + [0.0] * (self.dimensions - 2))
        return SimpleNamespace(
            vectors=vectors, source=self.source, model_id=self.model_id,
            dimensions=self.dimensions, fallback_reason=None,
        )


@pytest.fixture
def runtime(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(routed_vectors, "get_model_routing", lambda: runtime)
    return runtime


async def seed():
    apple = await note_service.create_note(
        title="Apple", markdown="apple orchard", folder=None, tags=[],
    )
    banana = await note_service.create_note(
        title="Banana", markdown="banana grove", folder=None, tags=[],
    )
    return apple, banana


def local_engine():
    return RetrievalEngine(HashEmbeddingProvider(), LexicalReranker(), SqliteVecStore())


def request(mode=SearchMode.vector):
    return SearchRequest(query="apple", mode=mode, limit=10)


def rows(sql, parameters=()):
    conn = connect()
    try:
        return conn.execute(sql, parameters).fetchall()
    finally:
        conn.close()


def test_api_index_and_query_use_matching_space_and_keep_local_metadata(runtime):
    async def scenario():
        apple, banana = await seed()
        result = await engine.search(request())
        assert result.items[0].note_id == banana.note_id
        baseline = await local_engine().search(request())
        assert baseline.items[0].note_id == apple.note_id
        assert rows("SELECT DISTINCT space_id, dimensions FROM routed_block_vectors")[0][:] == ("space-a", 3)
        assert rows("SELECT COUNT(*) FROM routed_block_vectors")[0][0] == len(apple.blocks) + len(banana.blocks)
        meta = repository.get_index_meta()
        assert meta["embedding_model"] == "hash-v1"
        assert meta["embedding_dim"] == "128"
        assert len(runtime.calls) == 3

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "local", "missing", "dimension", "corrupt"])
def test_query_falls_back_to_exact_local_results(runtime, failure):
    async def scenario():
        await seed()
        if failure == "exception":
            runtime.error = RuntimeError("offline")
        elif failure == "local":
            runtime.source = "local"
        elif failure == "missing":
            rows("DELETE FROM routed_block_vectors WHERE block_id = (SELECT MIN(block_id) FROM blocks)")
        elif failure == "dimension":
            runtime.dimensions = 4
        else:
            rows("UPDATE routed_block_vectors SET vector = ?", ("[0, 0, 0]",))
        actual = await engine.search(request())
        baseline = await local_engine().search(request())
        assert actual == baseline

    asyncio.run(scenario())


def test_same_dimension_model_switch_never_combines_partial_spaces(runtime):
    async def scenario():
        apple, banana = await seed()
        baseline = await local_engine().search(request())
        runtime.model_id = "space-b"
        assert await engine.search(request()) == baseline
        await note_service.update_note(apple.note_id, markdown="apple orchard")
        assert {row[0] for row in rows("SELECT DISTINCT space_id FROM routed_block_vectors")} == {"space-a", "space-b"}
        assert await routed_vectors.search_remote("apple", top_k=10) is None
        assert await engine.search(request()) == baseline
        runtime.model_id = "space-a"
        assert await engine.search(request()) == baseline
        runtime.model_id = "space-b"
        await note_service.update_note(banana.note_id, markdown="banana grove")
        hits = await routed_vectors.search_remote("apple", top_k=10)
        assert hits is not None and hits[0].id == banana.blocks[0].block_id
        assert (await engine.search(request())).items[0].note_id == banana.note_id

    asyncio.run(scenario())


def test_complete_spaces_coexist_but_only_requested_space_is_ranked(runtime):
    async def scenario():
        apple, banana = await seed()
        conn = connect()
        try:
            with transaction(conn):
                routed_vectors.store_remote(
                    conn, [apple.blocks[0].block_id, banana.blocks[0].block_id],
                    routed_vectors.RemoteEmbeddings("space-b", 3, [[1, 0, 0], [0, 1, 0]]),
                )
        finally:
            conn.close()
        assert (await engine.search(request())).items[0].note_id == banana.note_id
        runtime.model_id = "space-b"
        result = await engine.search(request())
        assert len(result.items) == 2
        assert result.items[0].note_id == apple.note_id

    asyncio.run(scenario())


def test_failed_note_embedding_preserves_save_and_forces_coverage_fallback(runtime):
    async def scenario():
        apple, banana = await seed()
        runtime.error = RuntimeError("offline")
        await note_service.update_note(banana.note_id, markdown="banana changed")
        assert (await note_service.get_note(banana.note_id)).markdown == "banana changed"
        assert rows("SELECT COUNT(*) FROM routed_block_vectors")[0][0] == len(apple.blocks)
        runtime.error = None
        assert await engine.search(request()) == await local_engine().search(request())

    asyncio.run(scenario())


@pytest.mark.parametrize("vectors, dimensions, space", [
    ([], 3, "space-a"),
    ([[1, 0]], 3, "space-a"),
    ([[0, 0, 0]], 3, "space-a"),
    ([[float("nan"), 0, 0]], 3, "space-a"),
    ([[float("inf"), 0, 0]], 3, "space-a"),
    ([[True, 0, 0]], 3, "space-a"),
    ([[1, 0, 0]], 0, "space-a"),
    ([[1, 0, 0]], 3, "hash-v1"),
])
def test_invalid_remote_batch_does_not_break_note_saving(runtime, vectors, dimensions, space):
    runtime.result_override = SimpleNamespace(
        source="api", vectors=vectors, dimensions=dimensions, model_id=space,
    )

    async def scenario():
        note = await note_service.create_note(title="Apple", markdown="apple orchard", folder=None, tags=[])
        assert (await local_engine().search(request())).items[0].note_id == note.note_id
        assert await routed_vectors.search_remote("apple", top_k=10) is None

    asyncio.run(scenario())


def test_remote_storage_failure_rolls_back_batch_but_keeps_local_index(runtime):
    async def scenario():
        await seed()
        rows("""CREATE TRIGGER reject_remote_vector BEFORE INSERT ON routed_block_vectors
                WHEN (SELECT content FROM blocks WHERE block_id = NEW.block_id) = 'second'
                BEGIN SELECT RAISE(ABORT, 'simulated storage failure'); END""")
        note = await note_service.create_note(
            title="Multi", markdown="first\n\nsecond", folder=None, tags=[],
        )
        assert len(note.blocks) == 2
        assert rows(
            "SELECT COUNT(*) FROM routed_block_vectors r JOIN blocks b USING(block_id) WHERE b.note_id = ?",
            (note.note_id,),
        )[0][0] == 0
        assert rows("SELECT COUNT(*) FROM vec_blocks")[0][0] == rows("SELECT COUNT(*) FROM blocks")[0][0]
        assert (get_settings().vault_path / note.file_path).exists()

    asyncio.run(scenario())


def test_rebuild_and_delete_clear_old_remote_rows_through_foreign_keys(runtime):
    async def scenario():
        apple, _ = await seed()
        await note_service.delete_note(apple.note_id)
        assert rows("SELECT COUNT(*) FROM routed_block_vectors")[0][0] == 1
        runtime.source = "local"
        job = await index_service.rebuild(IndexRebuildRequest())
        assert job.status == "completed"
        assert rows("SELECT COUNT(*) FROM routed_block_vectors")[0][0] == 0
        assert rows("SELECT COUNT(*) FROM vec_blocks")[0][0] == 1
        runtime.source = "api"
        runtime.model_id = "space-b"
        await index_service.rebuild(IndexRebuildRequest())
        assert [row[0] for row in rows("SELECT space_id FROM routed_block_vectors")] == ["space-b"]

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["save", "query", "rebuild"])
def test_cancellation_propagates_and_mutations_roll_back(runtime, operation):
    async def scenario():
        apple, _ = await seed()
        before = [tuple(row) for row in rows("SELECT * FROM routed_block_vectors ORDER BY block_id")]
        runtime.error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            if operation == "query":
                await engine.search(request())
            elif operation == "rebuild":
                await index_service.rebuild(IndexRebuildRequest())
            else:
                await note_service.update_note(apple.note_id, markdown="changed")
        assert (await note_service.get_note(apple.note_id)).markdown == "apple orchard"
        assert [tuple(row) for row in rows("SELECT * FROM routed_block_vectors ORDER BY block_id")] == before

    asyncio.run(scenario())


@pytest.mark.parametrize("injected", ["embedding", "vector_store", "constructor"])
def test_injected_engine_dependencies_are_respected(runtime, monkeypatch, injected):
    async def scenario():
        apple, _ = await seed()
        target = engine
        if injected == "constructor":
            target = local_engine()
        elif injected == "embedding":
            monkeypatch.setattr(engine, "embedding", HashEmbeddingProvider())
        else:
            class FakeStore:
                async def search(self, vector, *, top_k):
                    assert len(vector) == 128
                    return [VectorHit(id=apple.blocks[0].block_id, score=1.0)]

            monkeypatch.setattr(engine, "vector_store", FakeStore())
        runtime.calls.clear()
        assert (await target.search(request())).items[0].note_id == apple.note_id
        assert runtime.calls == []

    asyncio.run(scenario())


def test_fts_skips_routing_and_hybrid_uses_routed_vector_channel(runtime, monkeypatch):
    async def scenario():
        _, banana = await seed()
        runtime.calls.clear()
        await engine.search(request(SearchMode.fts))
        assert runtime.calls == []
        # Empty lexical channel isolates the vector contribution to hybrid fusion.
        monkeypatch.setattr(repository, "fts_search", lambda *_: [])

        class PreserveOrder:
            async def rerank(self, query, candidates):
                return sorted(candidates, key=lambda candidate: -candidate.score)

        monkeypatch.setattr(engine, "reranker", PreserveOrder())
        result = await engine.search(request(SearchMode.hybrid))
        assert result.items[0].note_id == banana.note_id
        assert runtime.calls == [["apple"]]

    asyncio.run(scenario())


def test_arbitrary_dimensions_and_extreme_finite_values(runtime):
    dimensions = 257
    runtime.result_override = SimpleNamespace(
        source="api", model_id="space-wide", dimensions=dimensions,
        vectors=[[1e308, 1e308] + [0.0] * (dimensions - 2)],
    )

    async def scenario():
        note = await note_service.create_note(title="Apple", markdown="apple orchard", folder=None, tags=[])
        hits = await routed_vectors.search_remote("apple", top_k=1)
        assert hits is not None and hits[0].id == note.blocks[0].block_id
        assert hits[0].score == pytest.approx(1.0)
        vector = json.loads(rows("SELECT vector FROM routed_block_vectors")[0][0])
        assert len(vector) == dimensions

    asyncio.run(scenario())


def test_missing_runtime_uses_unchanged_local_retrieval(runtime, monkeypatch):
    monkeypatch.setattr(routed_vectors, "get_model_routing", lambda: None)

    async def scenario():
        await seed()
        assert await engine.search(request()) == await local_engine().search(request())
        assert runtime.calls == []

    asyncio.run(scenario())
