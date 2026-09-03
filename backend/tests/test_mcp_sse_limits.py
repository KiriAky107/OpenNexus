import json
from contextlib import closing

import httpx
import pytest

from app.extensions import mcp


class ChunkStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.bytes_read = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.bytes_read += len(chunk)
            yield chunk


def test_sse_rejects_unterminated_line_before_reading_entire_stream(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_MCP_MESSAGE_BYTES", 1024)
    stream = ChunkStream([b"x" * 256] * 256)
    with (
        closing(httpx.Response(200, stream=stream)) as response,
        pytest.raises(mcp.McpBridgeError, match="too large"),
    ):
        list(mcp._iter_sse(response))
    assert stream.bytes_read == 1280


def test_sse_limits_combined_event_before_partial_line_is_complete(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_MCP_MESSAGE_BYTES", 32)
    stream = ChunkStream(
        [b"data: 123456789\n", b"data: 123456789\n", b"x", b"not-read"]
    )
    with (
        closing(httpx.Response(200, stream=stream)) as response,
        pytest.raises(mcp.McpBridgeError, match="too large"),
    ):
        list(mcp._iter_sse(response))
    assert stream.bytes_read == 33


@pytest.mark.parametrize("separator", [b"\n", b"\r", b"\r\n"])
@pytest.mark.parametrize("chunk_size", [1, 2, 7, 1024])
def test_sse_preserves_utf8_and_line_endings_across_chunks(separator, chunk_size):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "result": {"text": "中文"}}, ensure_ascii=False
    )
    wire = b"\xef\xbb\xbf" + separator.join(
        [
            b": heartbeat",
            b"event: message",
            b"id: replay-1",
            ("data: " + payload).encode(),
            b"",
            b"",
        ]
    )
    stream = ChunkStream(
        [wire[index : index + chunk_size] for index in range(0, len(wire), chunk_size)]
    )
    with closing(httpx.Response(200, stream=stream)) as response:
        assert list(mcp._iter_sse(response)) == [("message", "replay-1", payload)]


def test_sse_event_limit_resets_between_events(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_MCP_MESSAGE_BYTES", 16)
    with closing(
        httpx.Response(200, stream=ChunkStream([b"data: one\n\ndata: two\r\r"]))
    ) as response:
        assert list(mcp._iter_sse(response)) == [
            ("message", None, "one"),
            ("message", None, "two"),
        ]


def test_sse_preserves_multiline_data_and_final_unterminated_line():
    with closing(
        httpx.Response(200, stream=ChunkStream([b"data: first\ndata: last"]))
    ) as response:
        assert list(mcp._iter_sse(response)) == [("message", None, "first\nlast")]
