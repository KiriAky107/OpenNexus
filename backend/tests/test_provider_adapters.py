import asyncio
import json

import httpx

from app.contracts import (
    Message,
    MessageRole,
    ModelEventType,
    ModelRequest,
    ToolCall,
    ToolDefinition,
)
from app.providers.ollama import OllamaProvider
from app.providers.base import ProviderError
from app.providers.credentials import EnvironmentCredentialResolver
from app.providers.openai_compatible import OpenAICompatibleProvider


class StaticCredentials:
    def resolve(self, credential_id: str | None) -> str | None:
        return "secret-test-key" if credential_id else None


def run(coroutine):
    return asyncio.run(coroutine)


async def collect(stream):
    return [event async for event in stream]


def test_openai_compatible_maps_tool_call_and_credentials() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-test-key"
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "math.add",
                                        "arguments": '{"left":1,"right":2}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.test/v1",
        credential_id="openai-test",
        credentials=StaticCredentials(),
        transport=httpx.MockTransport(handler),
    )
    turn = run(
        provider.complete(
            ModelRequest(
                provider_id="test",
                model="test-model",
                messages=[Message(role=MessageRole.user, content="add")],
                tools=[
                    ToolDefinition(
                        name="math.add",
                        description="Add numbers",
                        parameters={"type": "object"},
                    )
                ],
            )
        )
    )

    assert captured["tools"][0]["function"]["name"].startswith("tool_")
    assert turn.tool_calls[0].name == "math.add"
    assert turn.tool_calls[0].arguments == {"left": 1, "right": 2}
    assert turn.input_tokens == 8


def test_openai_compatible_preserves_tool_call_context() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "done"}}],
                "usage": {},
            },
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.test/v1",
        credential_id=None,
        credentials=StaticCredentials(),
        transport=httpx.MockTransport(handler),
    )
    call = ToolCall(
        tool_call_id="call_1", name="math.add", arguments={"left": 1, "right": 2}
    )
    turn = run(
        provider.complete(
            ModelRequest(
                provider_id="test",
                model="test-model",
                messages=[
                    Message(role=MessageRole.user, content="add"),
                    Message(role=MessageRole.assistant, content="", tool_calls=[call]),
                    Message(
                        role=MessageRole.tool,
                        content='{"value":3}',
                        name="math.add",
                        tool_call_id="call_1",
                    ),
                ],
            )
        )
    )

    assert captured["messages"][1]["tool_calls"][0]["id"] == "call_1"
    assert captured["messages"][2]["tool_call_id"] == "call_1"
    assert turn.text == "done"


def test_openai_compatible_fetches_and_maps_model_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer secret-test-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}]},
        )

    provider = OpenAICompatibleProvider(
        base_url="https://provider.test/v1",
        credential_id="provider-test",
        credentials=StaticCredentials(),
        transport=httpx.MockTransport(handler),
    )

    models = run(provider.list_models())

    assert [item.model for item in models] == ["model-b", "model-a"]


def test_environment_credentials_support_deepseek_development_alias(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-test-key")

    assert EnvironmentCredentialResolver().resolve("deepseek") == "secret-test-key"


def test_openai_compatible_rejects_missing_named_credential_before_request() -> None:
    class EmptyCredentials:
        def resolve(self, credential_id: str | None) -> str | None:
            return None

    provider = OpenAICompatibleProvider(
        base_url="https://provider.test/v1",
        credential_id="deepseek",
        credentials=EmptyCredentials(),
    )

    try:
        run(provider.list_models())
    except ProviderError as error:
        assert error.code == "PROVIDER_CREDENTIAL_MISSING"
    else:
        raise AssertionError("Missing credential should fail before the provider request")


def test_ollama_maps_models_and_completion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:latest"}]})
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "local answer"},
                "prompt_eval_count": 5,
                "eval_count": 2,
            },
        )

    provider = OllamaProvider(transport=httpx.MockTransport(handler))
    models = run(provider.list_models())
    turn = run(
        provider.complete(
            ModelRequest(
                provider_id="ollama",
                model="qwen3:latest",
                messages=[Message(role=MessageRole.user, content="hello")],
            )
        )
    )

    assert models[0].model == "qwen3:latest"
    assert turn.text == "local answer"
    assert turn.input_tokens == 5
    assert turn.output_tokens == 2


def test_openai_compatible_streams_incremental_sse() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        body = "\n".join(
            [
                'data: {"choices":[{"delta":{"content":"hel"}}]}',
                'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1}}',
                "data: [DONE]",
                "",
            ]
        )
        return httpx.Response(200, text=body)

    provider = OpenAICompatibleProvider(
        base_url="https://provider.test/v1",
        credential_id=None,
        credentials=StaticCredentials(),
        transport=httpx.MockTransport(handler),
    )
    request = ModelRequest(
        provider_id="test", model="model",
        messages=[Message(role=MessageRole.user, content="hello")],
    )
    events = run(collect(provider.stream(request)))

    assert [item.data["text"] for item in events if item.event == ModelEventType.text_delta] == [
        "hel", "lo"
    ]
    assert events[-1].event == ModelEventType.done
    assert [item.sequence for item in events] == list(range(len(events)))


def test_ollama_streams_incremental_jsonl() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        body = "\n".join(
            [
                '{"message":{"content":"本"},"done":false}',
                '{"message":{"content":"地"},"done":false}',
                '{"message":{"content":""},"done":true,"prompt_eval_count":3,"eval_count":2}',
                "",
            ]
        )
        return httpx.Response(200, text=body)

    provider = OllamaProvider(transport=httpx.MockTransport(handler))
    request = ModelRequest(
        provider_id="ollama", model="qwen",
        messages=[Message(role=MessageRole.user, content="hello")],
    )
    events = run(collect(provider.stream(request)))

    assert [item.data["text"] for item in events if item.event == ModelEventType.text_delta] == [
        "本", "地"
    ]
    assert events[-2].event == ModelEventType.usage
    assert events[-1].event == ModelEventType.done
