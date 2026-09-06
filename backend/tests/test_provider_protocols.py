"""Wire-level provider tests: no credentials, SDKs, clocks, or network services."""

import asyncio
import json

import httpx
import pytest

from app.contracts import Message, MessageRole, ModelCapability, ModelEventType as E, ModelRequest, ToolCall, ToolDefinition
from app.providers.anthropic_messages import AnthropicMessagesProvider
from app.providers.base import ProviderError
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider
from app.providers.openai_responses import OpenAIResponsesProvider


NATIVE = ["responses", "anthropic"]
PROTOCOLS = [*NATIVE, "compatible", "ollama"]
SECRET = "test-only-sensitive-upstream-body"


class Credentials:
    def resolve(self, credential_id):
        return SECRET if credential_id else None


class Bytes(httpx.AsyncByteStream):
    def __init__(self, body: bytes, *, fragment: int = 17):
        self.body = body
        self.fragment = fragment
        self.closed = False

    async def __aiter__(self):
        for offset in range(0, len(self.body), self.fragment):
            yield self.body[offset:offset + self.fragment]

    async def aclose(self):
        self.closed = True


class GatedBytes(Bytes):
    def __init__(self, body):
        super().__init__(body)
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()

    async def __aiter__(self):
        yield self.body
        self.waiting.set()
        await self.release.wait()


def provider(protocol, handler, *, credential_id="test"):
    transport = httpx.MockTransport(handler)
    if protocol == "ollama":
        return OllamaProvider("https://provider.test", transport=transport)
    cls = {"responses": OpenAIResponsesProvider, "anthropic": AnthropicMessagesProvider,
           "compatible": OpenAICompatibleProvider}[protocol]
    return cls("https://provider.test/v1/", credential_id, Credentials(), transport=transport)


def request(*, history=False):
    messages = [Message(role=MessageRole.user, content="查笔记")]
    if history:
        messages += [
            Message(role=MessageRole.system, content="Additional rules"),
            Message(role=MessageRole.assistant, content="Checking", tool_calls=[
                ToolCall(tool_call_id="old_1", name="lookup", arguments={"query": "a"}),
                ToolCall(tool_call_id="old_2", name="lookup", arguments={"query": "b"}),
            ]),
            Message(role=MessageRole.tool, tool_call_id="old_1", content='{"found":1}'),
            Message(role=MessageRole.tool, tool_call_id="old_2", content='{"found":2}'),
        ]
    return ModelRequest(
        provider_id="test", model="model", system="System rules", messages=messages,
        tools=[ToolDefinition(name="lookup", description="Find notes", parameters={"type": "object"})],
        max_tokens=512, temperature=0,
    )


async def collect(iterator):
    return [event async for event in iterator]


@pytest.mark.parametrize("name", ["lookup", "notes.search"])
def test_compatible_split_tool_name_preserves_identity(name):
    from app.providers.tool_names import prepare_tool_names
    req = request()
    req.tools[0].name = name
    wire, _ = prepare_tool_names(req)
    alias = wire.tools[0].name

    def handler(_):
        return httpx.Response(200, content=sse(
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1",
                "function": {"name": alias[:3], "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0,
                "function": {"name": alias[3:], "arguments": '{"query":"x"}'}}]},
                "finish_reason": "tool_calls"}]},
            {"type": "[DONE]"},
        ))

    events = asyncio.run(collect(provider("compatible", handler).stream(req)))
    assert [e.data["name"] for e in events if e.event == E.tool_call_start] == [name]
    assert json.loads("".join(e.data["arguments_delta"] for e in events
                             if e.event == E.tool_call_delta)) == {"query": "x"}
    assert events[-1].data["status"] == "completed"


def sse(*events):
    return "".join(
        f"event: {event.get('type', 'message')}\r\ndata: {json.dumps(event, ensure_ascii=False)}\r\n\r\n"
        for event in events
    ).encode()


def wire(protocol, *events):
    if protocol == "ollama":
        return ("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n").encode()
    return sse(*events)


def start(protocol):
    if protocol == "responses":
        return [{"type": "response.output_text.delta", "delta": "你好"}]
    if protocol == "anthropic":
        return [{"type": "message_start", "message": {"usage": {"input_tokens": 7, "output_tokens": 0}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}}]
    if protocol == "compatible":
        return [{"choices": [{"delta": {"content": "你好"}}]}]
    return [{"message": {"content": "你好"}, "done": False}]


def terminal(protocol):
    if protocol == "responses":
        return [{"type": "response.completed", "response": {"status": "completed", "usage": {"input_tokens": 7, "output_tokens": 2}}}]
    if protocol == "anthropic":
        return [{"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
                {"type": "message_stop"}]
    if protocol == "compatible":
        return [{"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}]
    return [{"message": {}, "done": True, "prompt_eval_count": 7, "eval_count": 2}]


def assert_events(events):
    assert events[-1].event == E.done
    assert events[-1].data["status"] == ("failed" if any(event.event == E.error for event in events) else "completed")
    assert sum(event.event == E.done for event in events) == 1
    assert [event.sequence for event in events] == list(range(len(events)))
    assert all(event.timestamp.tzinfo is not None for event in events)


def assert_error(events, code):
    assert_events(events)
    assert events[-2].event == E.error
    assert events[-2].data["code"] == code
    assert SECRET not in str(events[-2].data)


@pytest.mark.parametrize("protocol", NATIVE)
def test_native_completion_and_history(protocol):
    captured = {}

    def handler(req):
        captured.update(json.loads(req.content))
        assert req.url.path == ("/v1/responses" if protocol == "responses" else "/v1/messages")
        if protocol == "responses":
            assert req.headers["authorization"] == f"Bearer {SECRET}"
            body = {"status": "completed", "output": [
                {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
                {"type": "message", "content": [{"type": "output_text", "text": "完成"}]},
                {"type": "function_call", "call_id": "next", "name": "lookup", "arguments": '{"query":"c"}'},
            ], "usage": {"input_tokens": 10, "output_tokens": 3}}
        else:
            assert "authorization" not in req.headers
            assert req.headers["x-api-key"] == SECRET
            assert req.headers["anthropic-version"] == "2023-06-01"
            body = {"type": "message", "content": [
                {"type": "thinking", "thinking": "thinking", "signature": "sig"},
                {"type": "text", "text": "完成"},
                {"type": "tool_use", "id": "next", "name": "lookup", "input": {"query": "c"}},
            ], "usage": {"input_tokens": 5, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 3}}
        return httpx.Response(200, json=body)

    turn = asyncio.run(provider(protocol, handler).complete(request(history=True)))
    assert turn.text == "完成"
    assert (turn.input_tokens, turn.output_tokens) == (10, 3)
    assert turn.tool_calls[0].tool_call_id == "next"
    assert turn.tool_calls[0].arguments == {"query": "c"}
    assert captured["stream"] is False
    assert captured["temperature"] == 0
    if protocol == "responses":
        assert captured["instructions"] == "System rules"
        assert captured["max_output_tokens"] == 512
        assert captured["tools"][0]["parameters"] == {"type": "object"}
        calls = [item for item in captured["input"] if item.get("type") == "function_call"]
        outputs = [item for item in captured["input"] if item.get("type") == "function_call_output"]
        assert [call["call_id"] for call in calls] == ["old_1", "old_2"]
        assert json.loads(calls[1]["arguments"]) == {"query": "b"}
        assert outputs == [{"type": "function_call_output", "call_id": "old_1", "output": '{"found":1}'},
                           {"type": "function_call_output", "call_id": "old_2", "output": '{"found":2}'}]
        assert {"role": "system", "content": "Additional rules"} in captured["input"]
    else:
        assert captured["system"] == "System rules\n\nAdditional rules"
        assert captured["max_tokens"] == 512
        assert captured["tools"][0]["input_schema"] == {"type": "object"}
        assert captured["messages"][1]["content"][2] == {
            "type": "tool_use", "id": "old_2", "name": "lookup", "input": {"query": "b"},
        }
        assert captured["messages"][-1] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "old_1", "content": '{"found":1}'},
            {"type": "tool_result", "tool_use_id": "old_2", "content": '{"found":2}'},
        ]}


def responses_tool_events():
    events = [
        {"type": "response.created", "response": {"usage": {"input_tokens": 10, "output_tokens": 0}}},
        {"type": "response.reasoning_summary_text.delta", "delta": "计划"},
        {"type": "response.output_text.delta", "delta": "查"},
        {"type": "response.output_text.delta", "delta": "找"},
    ]
    for index in (2, 3):
        events.append({"type": "response.output_item.added", "output_index": index, "item": {
            "id": f"item_{index}", "type": "function_call", "call_id": f"call_{index}", "name": "lookup", "arguments": "",
        }})
    for index, fragment in [(2, '{"query":'), (3, '{}'), (2, '"笔记"}')]:
        events.append({"type": "response.function_call_arguments.delta", "output_index": index,
                       "item_id": f"item_{index}", "delta": fragment})
    for index, arguments in [(3, '{}'), (2, '{"query":"笔记"}')]:
        events += [
            {"type": "response.function_call_arguments.done", "output_index": index, "item_id": f"item_{index}", "arguments": arguments},
            {"type": "response.output_item.done", "output_index": index, "item": {
                "id": f"item_{index}", "type": "function_call", "call_id": f"call_{index}", "name": "lookup", "arguments": arguments,
            }},
        ]
    events += [{"type": "future.event"}, {"type": "response.completed", "response": {
        "status": "completed", "usage": {"input_tokens": 10, "output_tokens": 9},
    }}]
    return events


def anthropic_tool_events():
    events = [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 5, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2, "output_tokens": 1,
        }}},
        {"type": "ping"},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "计划"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "查"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "找"}},
        {"type": "content_block_stop", "index": 1},
    ]
    for index, fragments in [(2, ['{"query":', '"笔记"}']), (3, [])]:
        events.append({"type": "content_block_start", "index": index, "content_block": {
            "type": "tool_use", "id": f"call_{index}", "name": "lookup", "input": {},
        }})
        for fragment in fragments:
            events.append({"type": "content_block_delta", "index": index,
                           "delta": {"type": "input_json_delta", "partial_json": fragment}})
        events.append({"type": "content_block_stop", "index": index})
    events += [
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 4}},
        {"type": "future.event"},
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    return events


@pytest.mark.parametrize("protocol", NATIVE)
def test_native_stream_tools_reasoning_usage_and_fragmented_utf8(protocol):
    frames = responses_tool_events() if protocol == "responses" else anthropic_tool_events()
    body = Bytes(b": comment\r\n\r\n" + sse(*frames) + b"data: malformed after completion\n\n", fragment=1)

    def handler(req):
        payload = json.loads(req.content)
        assert payload["stream"] is True
        assert payload["tools"]
        assert (payload.get("input") or payload.get("messages"))
        return httpx.Response(200, stream=body)

    events = asyncio.run(collect(provider(protocol, handler).stream(request(history=True))))
    assert_events(events)
    assert not any(event.event == E.error for event in events)
    assert [event.data["text"] for event in events if event.event == E.text_delta] == ["查", "找"]
    assert [event.data["text"] for event in events if event.event == E.thinking_delta] == ["计划"]
    assert [event.data["tool_call_id"] for event in events if event.event == E.tool_call_start] == ["call_2", "call_3"]
    assert sorted(event.data["tool_call_id"] for event in events if event.event == E.tool_call_end) == ["call_2", "call_3"]
    for call_id, expected in [("call_2", {"query": "笔记"}), ("call_3", {})]:
        arguments = "".join(event.data["arguments_delta"] for event in events
                            if event.event == E.tool_call_delta and event.data["tool_call_id"] == call_id)
        assert json.loads(arguments) == expected
    usages = [event.data for event in events if event.event == E.usage]
    assert usages[-1] == {"input_tokens": 10, "output_tokens": 9, "total_tokens": 19}
    assert all(usage["input_tokens"] == 10 for usage in usages)
    if protocol == "anthropic":
        assert [usage["output_tokens"] for usage in usages] == [1, 4, 9]
    assert body.closed


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_stream_terminal_usage_and_closure(protocol):
    body = Bytes(wire(protocol, *start(protocol), *terminal(protocol)))
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, stream=body)).stream(request())))
    assert_events(events)
    assert not any(event.event == E.error for event in events)
    assert [event.data["text"] for event in events if event.event == E.text_delta] == ["你好"]
    assert [event.data for event in events if event.event == E.usage][-1] == {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    assert body.closed


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("empty", [False, True])
def test_truncated_stream(protocol, empty):
    body = Bytes(b"" if empty else wire(protocol, *start(protocol)))
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, stream=body)).stream(request())))
    assert_error(events, "PROVIDER_STREAM_TRUNCATED")
    assert body.closed


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("bad", [b"not-json", b"[]", b"null", b'{"usage":'])
def test_malformed_stream_is_sanitized(protocol, bad):
    suffix = bad + b"\n" if protocol == "ollama" else b"data: " + bad + b"\n\n"
    body = Bytes(wire(protocol, *start(protocol)) + suffix)
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, stream=body)).stream(request())))
    assert_error(events, "PROVIDER_INVALID_RESPONSE")
    assert body.closed


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("error_type,code", [("rate_limit_error", "PROVIDER_RATE_LIMITED"),
                                           ("authentication_error", "PROVIDER_AUTH_FAILED"),
                                           ("overloaded_error", "PROVIDER_UNAVAILABLE")])
def test_in_band_error_after_partial_output(protocol, error_type, code):
    body = Bytes(wire(protocol, *start(protocol), {"type": "error", "error": {"type": error_type, "message": SECRET}}))
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, stream=body)).stream(request())))
    assert any(event.event == E.text_delta for event in events)
    assert_error(events, code)
    assert body.closed


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("status,code", [(400, "PROVIDER_INVALID_REQUEST"), (401, "PROVIDER_AUTH_FAILED"),
                                       (403, "PROVIDER_AUTH_FAILED"), (404, "MODEL_NOT_FOUND"),
                                       (429, "PROVIDER_RATE_LIMITED"), (500, "PROVIDER_UNAVAILABLE")])
def test_http_errors_completion_and_stream(protocol, status, code):
    adapter = provider(protocol, lambda _: httpx.Response(status, text=SECRET))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(adapter.complete(request()))
    assert exc.value.code == code
    assert SECRET not in str(exc.value)
    assert_error(asyncio.run(collect(adapter.stream(request()))), code)


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("body,code", [(b"broken", "PROVIDER_INVALID_RESPONSE"),
                                     (b"[]", "PROVIDER_INVALID_RESPONSE"),
                                     (b"{}", "PROVIDER_INVALID_RESPONSE"),
                                     (json.dumps({"error": {"code": "invalid_api_key", "message": SECRET}}).encode(), "PROVIDER_AUTH_FAILED")])
def test_bad_completion(protocol, body, code):
    adapter = provider(protocol, lambda _: httpx.Response(200, content=body))
    with pytest.raises(ProviderError) as exc:
        asyncio.run(adapter.complete(request()))
    assert exc.value.code == code
    assert SECRET not in str(exc.value)


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("error,code", [(httpx.ReadTimeout, "PROVIDER_TIMEOUT"),
                                      (httpx.ConnectError, "PROVIDER_UNAVAILABLE")])
def test_transport_error_mapping(protocol, error, code):
    def handler(req):
        raise error(SECRET, request=req)

    adapter = provider(protocol, handler)
    with pytest.raises(ProviderError) as exc:
        asyncio.run(adapter.complete(request()))
    assert exc.value.code == code
    assert SECRET not in str(exc.value)
    assert_error(asyncio.run(collect(adapter.stream(request()))), code)


@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("cancel", [True, False])
def test_incremental_delivery_cancellation_and_explicit_close(protocol, cancel):
    async def scenario():
        body = GatedBytes(wire(protocol, *start(protocol)))
        adapter = provider(protocol, lambda _: httpx.Response(200, stream=body))
        iterator = adapter.stream(request())
        seen = []
        while True:
            event = await asyncio.wait_for(anext(iterator), timeout=1)
            seen.append(event)
            if event.event == E.text_delta:
                break
        # The first token arrives while the response is still open and blocked.
        assert seen[-1].data["text"] == "你好"
        assert not body.closed
        if cancel:
            pending = asyncio.create_task(anext(iterator))
            await asyncio.wait_for(body.waiting.wait(), timeout=1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await iterator.aclose()
        assert body.closed
        assert not any(event.event in {E.error, E.done} for event in seen)

    asyncio.run(scenario())


@pytest.mark.parametrize("protocol", NATIVE)
def test_cancellation_before_response_headers(protocol):
    async def scenario():
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def handler(req):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

        adapter = provider(protocol, handler)
        pending = asyncio.create_task(adapter.complete(request()))
        await asyncio.wait_for(entered.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert closed.is_set()

    asyncio.run(scenario())


@pytest.mark.parametrize("protocol", NATIVE)
def test_native_discovery_does_not_claim_non_chat_capabilities(protocol):
    def handler(req):
        assert req.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": name} for name in ["chat-model", "text-embedding-3-small", "whisper-1", "gpt-audio"]]})

    models = asyncio.run(provider(protocol, handler).list_models())
    assert ModelCapability.chat in models[0].capabilities
    assert models[1].capabilities == [ModelCapability.embedding]
    assert all(ModelCapability.chat not in model.capabilities for model in models[1:])


@pytest.mark.parametrize("protocol", NATIVE)
def test_native_structured_format_mapping(protocol):
    adapter = provider(protocol, lambda _: pytest.fail("No network expected"))
    req = request()
    req.response_format = {"type": "json_schema", "json_schema": {
        "name": "answer", "strict": True, "schema": {"type": "object", "properties": {}},
    }}
    payload = adapter._payload(req, stream=False)
    format_ = payload["text"]["format"] if protocol == "responses" else payload["output_config"]["format"]
    assert format_["type"] == "json_schema"
    assert format_["schema"] == {"type": "object", "properties": {}}
    if protocol == "responses":
        assert format_["name"] == "answer"
        assert format_["strict"] is True


@pytest.mark.parametrize("protocol", NATIVE)
def test_invalid_tool_arguments_and_unclosed_tool(protocol):
    frames = responses_tool_events() if protocol == "responses" else anthropic_tool_events()
    # A syntactically valid terminal cannot rescue an unfinished tool block.
    index = next(i for i, frame in enumerate(frames)
                 if frame["type"] in {"response.function_call_arguments.delta", "content_block_delta"}
                 and (frame.get("output_index") == 2 or frame.get("index") == 2))
    partial = frames[:index + 1]
    final = frames[-1]
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, content=sse(*partial, final))).stream(request())))
    assert_error(events, "PROVIDER_STREAM_TRUNCATED")
    assert not any(event.event == E.tool_call_end for event in events)

    for frame in frames:
        if frame["type"] == "response.function_call_arguments.done":
            frame["arguments"] = "[]"
            break
        if frame["type"] == "content_block_delta" and frame.get("index") == 2:
            frame["delta"]["partial_json"] = "malformed"
            break
    events = asyncio.run(collect(provider(protocol, lambda _: httpx.Response(200, content=sse(*frames))).stream(request())))
    assert_error(events, "PROVIDER_INVALID_RESPONSE")


@pytest.mark.parametrize("kind,code", [("response.failed", "PROVIDER_UNAVAILABLE"),
                                     ("response.incomplete", "PROVIDER_INCOMPLETE_RESPONSE")])
def test_responses_failed_and_incomplete(kind, code):
    frame = {"type": kind, "response": {"status": kind.split(".")[1], "incomplete_details": {"reason": SECRET}}}
    events = asyncio.run(collect(provider("responses", lambda _: httpx.Response(200, content=sse(*start("responses"), frame))).stream(request())))
    assert_error(events, code)


def test_sse_multiline_data_and_event_name_without_json_type():
    body = (b': keepalive\n\nevent: response.output_text.delta\ndata: {\ndata: "delta": "hello"\ndata: }\n\n'
            + sse({"type": "response.completed", "response": {"status": "completed"}}))
    events = asyncio.run(collect(provider("responses", lambda _: httpx.Response(200, content=body)).stream(request())))
    assert_events(events)
    assert [event.data["text"] for event in events if event.event == E.text_delta] == ["hello"]
    assert not any(event.event == E.error for event in events)


def test_ollama_history_options_and_in_band_string_error():
    captured = {}

    def handler(req):
        captured.update(json.loads(req.content))
        return httpx.Response(200, json={"error": SECRET})

    with pytest.raises(ProviderError) as exc:
        asyncio.run(provider("ollama", handler).complete(request(history=True)))
    assert exc.value.code == "PROVIDER_UNAVAILABLE"
    assert SECRET not in str(exc.value)
    assert captured["messages"][-1]["tool_name"] == "lookup"
    assert captured["options"] == {"temperature": 0.0, "num_predict": 512}

@pytest.mark.parametrize("protocol", PROTOCOLS)
@pytest.mark.parametrize("streaming", [False, True])
def test_namespaced_tools_roundtrip_without_changing_internal_request(protocol, streaming):
    import re
    model_request = request(history=True)
    original_name = "mcp.my-server.search.notes"
    model_request.tools[0].name = original_name
    for message in model_request.messages:
        for call in message.tool_calls:
            call.name = original_name
    before = model_request.model_dump()

    def handler(req):
        payload = json.loads(req.content)
        definition = payload["tools"][0]
        name = (definition.get("function") or definition)["name"]
        assert name != original_name and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
        assert original_name not in req.content.decode()
        if protocol == "responses":
            item = {"type": "function_call", "id": "item1", "call_id": "call1", "name": name, "arguments": "{}"}
            body = {"status": "completed", "output": [item]}
            events = [
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": {"status": "completed"}},
            ]
        elif protocol == "anthropic":
            item = {"type": "tool_use", "id": "call1", "name": name, "input": {}}
            body = {"content": [item]}
            events = [
                {"type": "message_start", "message": {}},
                {"type": "content_block_start", "index": 0, "content_block": item},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_stop"},
            ]
        elif protocol == "compatible":
            item = {"id": "call1", "function": {"name": name, "arguments": "{}"}}
            body = {"choices": [{"message": {"tool_calls": [item]}}]}
            events = [{"choices": [{"delta": {"tool_calls": [{"index": 0, **item}]}, "finish_reason": "tool_calls"}]}]
        else:
            item = {"function": {"name": name, "arguments": {}}}
            body = {"message": {"tool_calls": [item]}, "done": True}
            events = [body]
        return httpx.Response(200, content=wire(protocol, *events)) if streaming else httpx.Response(200, json=body)

    adapter = provider(protocol, handler)
    if streaming:
        events = asyncio.run(collect(adapter.stream(model_request)))
        assert_events(events)
        assert [event.data["name"] for event in events if event.event == E.tool_call_start] == [original_name]
    else:
        assert asyncio.run(adapter.complete(model_request)).tool_calls[0].name == original_name
    assert model_request.model_dump() == before

def test_chat_route_closes_upstream_and_sanitizes_unexpected_errors(monkeypatch):
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from app import routes
    from app.contracts import ChatRequest, ModelEvent
    closed = []

    class Adapter:
        async def stream(self, request):
            try:
                yield ModelEvent(event=E.text_delta, sequence=0, data={"text": "first"}, timestamp=datetime.now(timezone.utc))
                raise RuntimeError(SECRET)
            finally:
                closed.append(True)

    monkeypatch.setattr(routes, "provider_or_404", lambda _: SimpleNamespace(adapter=Adapter()))

    async def scenario():
        response = await routes.chat(ChatRequest(provider_id="test", model="test", messages=[], use_rag=False))
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()
        assert len(closed) == 1
        response = await routes.chat(ChatRequest(provider_id="test", model="test", messages=[], use_rag=False))
        items = [json.loads(chunk.split("data: ")[1].strip()) async for chunk in response.body_iterator]
        assert [item["sequence"] for item in items] == [0, 1, 2]
        assert items[-1]["data"]["status"] == "failed"
        assert SECRET not in str(items)
        assert len(closed) == 2

    asyncio.run(scenario())
