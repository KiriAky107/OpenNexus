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


def test_desktop_persona_uses_bound_host_cas_and_retains_legacy(monkeypatch):
    from app.services import persona_settings, desktop_notes
    from app import host_bridge
    save_persona(PersonaSettings(system_prompt="legacy global"))
    monkeypatch.setattr(persona_settings, '_desktop', lambda: True)
    calls = []
    document = {'record': {'data': {'version': 7, 'name': 'Vault persona',
                'system_prompt': 'Scoped prompt', 'dialogue_pairs': []}}, 'hash': 'a' * 64}
    def call(method, **params):
        calls.append((method, params))
        if method == 'persona.get':
            return document
        if params['expected'] != document['hash']:
            raise ApiError(409, 'REVISION_CONFLICT', 'controlled stale hash')
        return {'record': params['record'], 'hash': 'b' * 64}
    monkeypatch.setattr(desktop_notes, 'call', call)
    loaded = load_persona()
    assert loaded.revision == 'a' * 64
    assert apply_global_persona(request()).system.endswith('Scoped prompt')
    token = host_bridge.operation_id.set('controlled-operation')
    try:
        saved = save_persona(loaded.model_copy(update={'name': 'Edited'}))
    finally:
        host_bridge.operation_id.reset(token)
    assert saved.version == 8 and saved.revision == 'b' * 64
    method, params = calls[-1]
    assert method == 'persona.write' and params['operation_id'] == 'controlled-operation'
    assert 'revision' not in params['record']['data']
    with pytest.raises(ApiError) as error:
        save_persona(loaded.model_copy(update={'revision': 'c' * 64}))
    assert error.value.code == 'PERSONA_VERSION_CONFLICT'
    monkeypatch.setattr(persona_settings, '_desktop', lambda: False)
    assert load_persona().system_prompt == 'legacy global'


def test_desktop_missing_persona_does_not_import_unowned_global_data(monkeypatch):
    from app.services import persona_settings, desktop_notes
    save_persona(PersonaSettings(system_prompt='unowned global data'))
    monkeypatch.setattr(persona_settings, '_desktop', lambda: True)
    monkeypatch.setattr(desktop_notes, 'call', lambda *args, **kwargs: None)
    assert load_persona() == PersonaSettings()
