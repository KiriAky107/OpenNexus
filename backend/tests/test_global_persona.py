import asyncio
import pytest
from app.contracts import ModelRequest, Message, ProviderConfig
from app.errors import ApiError
from app.services.persona_settings import PersonaSettings, DialoguePair, save_persona, load_persona, apply_global_persona


def request():
    return ModelRequest(provider_id="p", model="test", system="任务要求", messages=[Message(role="user", content="hello")])


def test_global_persona_persists_and_keeps_task_prompt():
    save_persona(PersonaSettings(name="老师", system_prompt="耐心解释", dialogue_pairs=[DialoguePair(user="问题", assistant="回答"), DialoguePair()]))
    assert load_persona().version == 1
    original = request()
    assembled = apply_global_persona(original)
    assert assembled.system == "任务要求\n\n全局人设 / Global persona\n耐心解释\n\n预设对话示例 / Example dialogue\nUser: 问题\nAssistant: 回答"
    assert original.system == "任务要求"
    with pytest.raises(ApiError):
        save_persona(PersonaSettings())


def test_empty_persona_omits_all_global_sections():
    save_persona(PersonaSettings(system_prompt="  ", dialogue_pairs=[DialoguePair(user=" ")]))
    assert apply_global_persona(request()).system == "任务要求"


def test_existing_provider_reads_latest_global_persona_for_complete_and_stream(monkeypatch):
    from app.providers.factory import ProviderFactory
    from app.providers.base import ProviderTurn
    seen = []
    class Adapter:
        async def complete(self, req):
            seen.append(req.system)
            return ProviderTurn(text="ok")
        async def stream(self, req):
            seen.append(req.system)
            if False: yield
    factory = ProviderFactory(None)
    monkeypatch.setattr(factory, "_build", lambda _: Adapter())
    adapter = factory.build(ProviderConfig(provider_id="p",name="test",provider_type="openai_compatible"))
    save_persona(PersonaSettings(system_prompt="全局人设"))
    async def run():
        await adapter.complete(request())
        async for _ in adapter.stream(request()): pass
    asyncio.run(run())
    assert len(seen) == 2
    assert all(text.count("全局人设 / Global persona") == 1 for text in seen)
