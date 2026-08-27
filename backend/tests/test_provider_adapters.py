import asyncio
import json

import httpx

from app.contracts import Message, MessageRole, ModelRequest, ToolCall, ToolDefinition
from app.providers.ollama import OllamaProvider
from app.providers.openai_compatible import OpenAICompatibleProvider


class StaticCredentials:
    def resolve(self, credential_id: str | None) -> str | None:
        return "secret-test-key" if credential_id else None


def run(coroutine):
    return asyncio.run(coroutine)


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

    assert captured["tools"][0]["function"]["name"] == "math.add"
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
