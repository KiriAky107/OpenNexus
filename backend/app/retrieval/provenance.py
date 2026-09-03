"""Task-local observations of the embedding path actually used by a search."""
from contextlib import contextmanager
from contextvars import ContextVar

_observation: ContextVar[dict | None] = ContextVar("embedding_observation", default=None)


@contextmanager
def capture_embedding():
    result = {"source": "not_used"}
    token = _observation.set(result)
    try:
        yield result
    finally:
        _observation.reset(token)


def record_embedding(**fields) -> None:
    result = _observation.get()
    if result is not None:
        result.update(fields)
