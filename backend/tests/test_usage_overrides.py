import asyncio
import json
from datetime import datetime, timedelta, timezone
from contextlib import closing

import httpx
import pytest
from pydantic import ValidationError

from app.contracts import ModelRequest, ProviderConfig, ProviderType
from app.providers.factory import ProviderFactory
from app.request_overrides import RequestOverride, apply_overrides
from app.services.usage_service import UsageAttempt, aggregate, connection


def summary():
    now = datetime.now(timezone.utc)
    return aggregate(now - timedelta(days=1), now + timedelta(days=1))


def test_cumulative_usage_deduplicates_and_missing_is_not_zero():
    attempt = UsageAttempt("test", "chat", "openai_compatible")
    attempt.observe({"usage": {"prompt_tokens": 100, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 75}}})
    attempt.persist()
    attempt.observe({"usage": {"completion_tokens": 5}})
    attempt.observe({"usage": {"completion_tokens": 3}})
    attempt.persist()
    incomplete = UsageAttempt("test", "chat", "openai_compatible")
    incomplete.persist()
    result = summary()
    assert result["request_count"] == 2
    assert result["totals"]["input_tokens"] == 100
    assert result["totals"]["output_tokens"] == 5
    assert result["totals"]["cache_write_tokens"] is None
    assert result["cache_hit_rate"] == .75
    assert result["coverage"]["input_tokens"] == 1


def test_anthropic_cache_is_added_once_and_raw_text_is_not_saved():
    attempt = UsageAttempt("test", "claude", "anthropic_messages")
    attempt.observe({"message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 20, "output_tokens": 0, "secret": "private text"}}})
    attempt.observe({"usage": {"output_tokens": 12}})
    attempt.persist()
    counts = summary()["totals"]
    assert counts["input_tokens"] == 110 and counts["total_tokens"] == 122
    assert counts["cache_miss_tokens"] == 10
    with closing(connection()) as conn:
        assert "private text" not in conn.execute("SELECT raw_json FROM model_usage").fetchone()[0]


def test_override_rules_merge_and_respect_capability_and_stream():
    rules = [RequestOverride(body={"stream_options": {"include_usage": True, "extra": 1}, "stop": ["one"]}),
             RequestOverride(model="special", stream=True, body={"stream_options": {"extra": 2}, "stop": ["two"], "temperature": None}),
             RequestOverride(capability="embedding", body={"dimensions": 384})]
    base = {"model": "special", "messages": [], "stream": True}
    result = apply_overrides(base, rules, "chat", stream=True)
    assert result["stream_options"] == {"include_usage": True, "extra": 2}
    assert result["stop"] == ["two"] and result["temperature"] is None
    assert "dimensions" not in result and "stop" not in base
    assert apply_overrides(base, rules, "chat")["stop"] == ["one"]


@pytest.mark.parametrize("body", [{"model":"other"}, {"messages":[]}, {"tools":[]}, {"stream":False},
    {"metadata":{"api_key":"hidden"}}, {"stream_options":{"include_usage": "false"}}])
def test_unsafe_or_invalid_overrides_are_rejected(body):
    with pytest.raises(ValidationError):
        RequestOverride(body=body)


def test_real_adapter_body_and_usage_persistence():
    class Credentials:
        def resolve(self, key):
            return None
    config = ProviderConfig(provider_id="wire", provider_type=ProviderType.openai_compatible, name="Wire", base_url="https://model.invalid/v1",
        request_overrides=[RequestOverride(stream=True, body={"stream_options":{"include_usage":False},"enable_thinking":False})])
    adapter = ProviderFactory(Credentials()).build(config)
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type":"text/event-stream"}, content=(
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":1}}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            'data: [DONE]\n\n'))
    adapter.transport = httpx.MockTransport(respond)
    async def consume():
        return [event async for event in adapter.stream(ModelRequest(provider_id="wire", model="special", messages=[]))]
    asyncio.run(consume())
    assert captured[0]["enable_thinking"] is False
    assert captured[0]["stream_options"]["include_usage"] is False
    result = summary()
    assert result["request_count"] == 1 and result["totals"]["input_tokens"] == 10
    assert result["complete_requests"] == 1
