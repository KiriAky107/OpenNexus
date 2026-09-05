import asyncio
from functools import wraps
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.contracts import Message, ModelContextPolicy, ModelRequest, ProviderConfig
from app.providers.base import ProviderError, ProviderTurn
from app.providers.context_budget import prepare_context
from app.providers.factory import ProviderFactory


def async_test(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return run


def config(mode="detect", **kwargs):
    return ProviderConfig(provider_id="p", provider_type="openai_compatible", name="test",
        context_policies=[ModelContextPolicy(model="test", context_window=8192, output_reserve=512,
                                            threshold=0.1, mode=mode, **kwargs)])


def request():
    return ModelRequest(provider_id="p", model="test", system="Keep this system instruction",
        messages=[Message(role="user", content="旧文本" * 500), Message(role="assistant", content="历史答复"),
                  Message(role="user", content="继续"), Message(role="assistant", content="近期答复"),
                  Message(role="user", content="最新问题")])


@async_test
async def test_threshold_detect_blocks_before_network():
    complete = AsyncMock()
    with pytest.raises(ProviderError, match="已达到") as error:
        await prepare_context(request(), config(), complete)
    assert error.value.code == "CONTEXT_COMPRESSION_REQUIRED"
    complete.assert_not_called()


@async_test
async def test_compress_preserves_archive_system_and_recent_turns():
    original = request()
    copy = original.model_dump()
    complete = AsyncMock(return_value=ProviderTurn(text="已讨论旧文本。"))
    prepared = await prepare_context(original, config("compress", prompt="自定义摘要指令"), complete)
    assert original.model_dump() == copy
    assert prepared.system == original.system
    assert prepared.messages[-3:] == original.messages[-3:]
    assert prepared.max_tokens == 512
    assert complete.call_args.args[0].system == "自定义摘要指令"
    assert not complete.call_args.args[0].tools


@async_test
async def test_unknown_model_unmodified():
    original = request().model_copy(update={"model": "other"})
    complete = AsyncMock()
    assert await prepare_context(original, config(), complete) is original
    complete.assert_not_called()


@async_test
async def test_single_oversize_turn_is_not_discarded():
    original = request().model_copy(update={"messages": request().messages[:1]})
    complete = AsyncMock()
    with pytest.raises(ProviderError, match="没有可压缩"):
        await prepare_context(original, config("compress"), complete)
    complete.assert_not_called()


@async_test
async def test_tool_history_is_not_split():
    original = request()
    original.messages.insert(2, Message(role="tool", content="result", tool_call_id="call"))
    complete = AsyncMock()
    with pytest.raises(ProviderError, match="工具调用历史"):
        await prepare_context(original, config("compress"), complete)
    complete.assert_not_called()


@async_test
async def test_ineffective_summary_fails_without_mutation():
    original = request()
    copy = original.model_dump()
    with pytest.raises(ProviderError, match="未缩短"):
        await prepare_context(original, config("compress"), AsyncMock(return_value=ProviderTurn(text="长" * 6000)))
    assert original.model_dump() == copy


@async_test
async def test_override_output_budget_is_counted():
    settings = config()
    from app.request_overrides import RequestOverride
    settings.request_overrides = [RequestOverride(body={"max_completion_tokens": 9000})]
    with pytest.raises(ProviderError, match="占满"):
        await prepare_context(request(), settings, AsyncMock())


@async_test
async def test_factory_stream_exposes_actionable_error_without_network():
    adapter = ProviderFactory(None).build(config())
    events = [event async for event in adapter.stream(request())]
    assert [e.event.value for e in events] == ["Error", "Done"]
    assert events[0].data["code"] == "CONTEXT_COMPRESSION_REQUIRED"


def test_invalid_and_duplicate_config_rejected():
    with pytest.raises(ValidationError):
        ModelContextPolicy(model="test", context_window=1024, output_reserve=1024)
    settings = config().model_dump()
    settings["context_policies"] *= 2
    with pytest.raises(ValidationError, match="同一模型"):
        ProviderConfig.model_validate(settings)


@async_test
async def test_factory_compression_status_and_usage_request_are_separate(monkeypatch):
    from datetime import datetime, timezone
    from app.contracts import ModelEvent, ModelEventType
    from app.services.usage_service import usage_context
    seen = []

    class Adapter:
        async def complete(self, req):
            seen.append((req, usage_context.get()))
            return ProviderTurn(text="历史摘要。")

        async def stream(self, req):
            seen.append((req, usage_context.get()))
            yield ModelEvent(event=ModelEventType.text_delta, timestamp=datetime.now(timezone.utc), data={"text": "回答"})
            yield ModelEvent(event=ModelEventType.done, timestamp=datetime.now(timezone.utc), data={"status": "completed"})

    factory = ProviderFactory(None)
    monkeypatch.setattr(factory, "_build", lambda _: Adapter())
    adapter = factory.build(config("compress"))
    original = request()
    events = [event async for event in adapter.stream(original)]
    assert [e.event.value for e in events] == ["ContextStatus", "TextDelta", "Done"]
    assert [e.sequence for e in events] == [0, 1, 2]
    assert seen[0][1]["request_id"] != seen[1][1]["request_id"]
    assert seen[1][0].messages[-3:] == original.messages[-3:]
