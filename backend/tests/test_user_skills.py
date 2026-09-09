from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest
from pydantic import ValidationError

from app import host_bridge
from app.contracts import ModelCapability, UserSkillWriteRequest
from app.errors import ApiError
from app.extensions import ExtensionError, SkillRuntime
from app.agent.tools import ToolRegistry
from app.services import user_skills
from app.sidecar import SessionAuth


class FakeTools:
    def __init__(self, permissions: dict[str, str | None]):
        self.permissions = permissions

    def contains(self, name: str) -> bool:
        return name in self.permissions

    def get(self, name: str):
        return SimpleNamespace(definition=SimpleNamespace(permission=self.permissions[name]))


def request(**updates) -> UserSkillWriteRequest:
    values = {
        "name": "Review notes",
        "description": "A portable declarative Skill",
        "prompt": "Review the selected note carefully.",
        "tools": ["notes.read"],
        "permissions": ["notes.read"],
        "required_capabilities": ["chat", "tool_calling"],
    }
    values.update(updates)
    return UserSkillWriteRequest.model_validate(values)


def test_user_skill_crud_uses_host_records_cas_and_idempotency(monkeypatch):
    tools = FakeTools({"notes.read": "notes.read"})
    documents: dict[str, dict] = {}
    operations: dict[str, dict] = {}
    calls = []

    def fake_call(method: str, **params):
        calls.append((method, params))
        if method == "user_skills.operation":
            return operations.get(params["operation_id"])
        if method == "user_skills.write":
            operation = params["operation_id"]
            if operation in operations:
                return operations[operation]
            skill_id = params["record"]["id"]
            current = documents.get(skill_id)
            actual = current["hash"] if current else ""
            if params["expected"] != actual:
                raise ApiError(409, "REVISION_CONFLICT", "conflict")
            digest = ("a" if current is None else "b") * 64
            result = {"record": params["record"], "hash": digest, "file_id": "file-1", "expected": params["expected"], "deleted": False}
            documents[skill_id] = result
            operations[operation] = result
            return result
        if method == "user_skills.get":
            return documents.get(params["id"])
        if method == "user_skills.list":
            values = list(documents.values())
            return {"items": values[params["offset"]:params["offset"] + params["limit"]], "total": len(values)}
        if method == "user_skills.delete":
            current = documents.get(params["id"])
            if not current or current["hash"] != params["expected"]:
                raise ApiError(409, "REVISION_CONFLICT", "conflict")
            removed = documents.pop(params["id"])
            result = {**removed, "expected": params["expected"], "deleted": True}
            operations[params["operation_id"]] = result
            return result
        raise AssertionError(method)

    monkeypatch.setattr(user_skills, "call", fake_call)
    token = host_bridge.operation_id.set("00000000-0000-4000-8000-000000000001")
    try:
        created = user_skills.create_user_skill(request(), tools)
    finally:
        host_bridge.operation_id.reset(token)
    assert created.skill_id.startswith("user_skill_")
    assert created.revision == "a" * 64
    assert created.data.version == 1
    assert created.status == "ready"
    first_write = next(params for method, params in calls if method == "user_skills.write")
    assert first_write["operation_id"] == "00000000-0000-4000-8000-000000000001"
    assert set(first_write["record"]["data"]) == {
        "version", "name", "description", "prompt", "tools", "permissions",
        "retrieval", "required_capabilities", "created_at_ms", "updated_at_ms",
    }
    token = host_bridge.operation_id.set("00000000-0000-4000-8000-000000000001")
    try:
        assert user_skills.create_user_skill(request(), tools) == created
        with pytest.raises(ApiError) as changed_replay:
            user_skills.create_user_skill(request(name="Different"), tools)
    finally:
        host_bridge.operation_id.reset(token)
    assert changed_replay.value.code == "USER_SKILL_OPERATION_CONFLICT"

    listed, total = user_skills.list_user_skills(tools, limit=100, offset=0)
    assert total == 1 and listed[0] == created
    updated = user_skills.update_user_skill(
        created.skill_id, request(revision=created.revision, name="Edited"), tools
    )
    assert updated.data.name == "Edited" and updated.data.version == 2
    with pytest.raises(ApiError, match="用户 Skill 已被其他设备修改") as conflict:
        user_skills.update_user_skill(
            created.skill_id, request(revision=created.revision, name="Stale"), tools
        )
    assert conflict.value.code == "USER_SKILL_REVISION_CONFLICT"
    user_skills.delete_user_skill(created.skill_id, updated.revision)
    assert user_skills.list_user_skills(tools, limit=100, offset=0)[1] == 0


def test_user_skill_declarations_are_validated_and_runtime_stays_device_gated(monkeypatch):
    tools = FakeTools({"notes.read": "notes.read", "notes.write": "notes.write"})
    document = {
        "record": {
            "schema": 1,
            "kind": "user_skill",
            "id": "user_skill_00000000000000000000000000000001",
            "data": {
                "version": 1,
                "name": "Writer",
                "description": "",
                "prompt": "Write only after confirmation.",
                "tools": ["notes.write"],
                "permissions": [],
                "retrieval": {"top_k": 10, "rerank": True, "citation": True},
                "required_capabilities": ["chat"],
                "created_at_ms": 1,
                "updated_at_ms": 1,
            },
        },
        "hash": "c" * 64,
        "file_id": "file-1",
    }
    monkeypatch.setattr(user_skills, "call", lambda method, **params: document)
    skill = user_skills.get_user_skill(document["record"]["id"], tools)
    assert skill.status == "permission_required"
    assert skill.undeclared_permissions == ["notes.write"]
    with pytest.raises(ApiError) as not_ready:
        user_skills.build_agent_configuration(
            skill.skill_id, [ModelCapability.chat], tools
        )
    assert not_ready.value.code == "USER_SKILL_NOT_READY"

    document["record"]["data"]["permissions"] = ["notes.write"]
    document["record"]["data"]["required_capabilities"] = ["vision"]
    with pytest.raises(ApiError) as missing_capability:
        user_skills.build_agent_configuration(skill.skill_id, [ModelCapability.chat], tools)
    assert missing_capability.value.code == "USER_SKILL_MODEL_CAPABILITY_MISSING"
    document["record"]["data"]["required_capabilities"] = ["chat"]
    config = user_skills.build_agent_configuration(skill.skill_id, [ModelCapability.chat], tools)
    assert config.allowed_tools == ["notes.write"]
    assert config.permissions == ["notes.write"]
    assert config.system_prompt == "Write only after confirmation."

    calls = []
    monkeypatch.setattr(user_skills, "call", lambda *args, **kwargs: calls.append((args, kwargs)))
    with pytest.raises(ApiError) as unknown:
        user_skills.create_user_skill(request(permissions=["secrets.export"]), tools)
    assert unknown.value.code == "USER_SKILL_PERMISSION_UNKNOWN"
    assert calls == []
    with pytest.raises(ValidationError):
        UserSkillWriteRequest.model_validate({
            **request().model_dump(), "api_key": "must-never-enter-a-record"
        })


def test_authenticated_sidecar_uses_uuid_idempotency_key_for_host_journal():
    seen = []

    async def app(scope, receive, send):
        seen.append((host_bridge.vault_id.get(), host_bridge.operation_id.get()))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def invoke(idempotency: bytes, request_id: bytes):
        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            return None

        scope = {
            "type": "http",
            "headers": [
                (b"authorization", b"Bearer secret"),
                (b"x-core-generation", b"generation"),
                (b"host", b"127.0.0.1:1234"),
                (b"x-opennexus-vault", b"00000000-0000-4000-8000-000000000010"),
                (b"x-request-id", request_id),
                (b"idempotency-key", idempotency),
            ],
        }
        await SessionAuth(app, "secret", "generation", 1234)(scope, receive, send)

    stable = b"00000000-0000-4000-8000-000000000020"
    fallback = b"00000000-0000-4000-8000-000000000030"
    asyncio.run(invoke(stable, fallback))
    asyncio.run(invoke(b"media-upload-key", fallback))
    asyncio.run(invoke(b"a" * 36, fallback))
    assert seen == [
        ("00000000-0000-4000-8000-000000000010", stable.decode()),
        ("00000000-0000-4000-8000-000000000010", fallback.decode()),
        ("00000000-0000-4000-8000-000000000010", fallback.decode()),
    ]


def test_installed_packages_cannot_claim_the_user_skill_record_namespace(tmp_path):
    package = tmp_path / "reserved"
    package.mkdir()
    (package / "skill.yaml").write_text(
        "skill_id: user_skill_00000000000000000000000000000001\n"
        "name: collision\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    with pytest.raises(ExtensionError) as error:
        SkillRuntime(ToolRegistry()).install(package)
    assert error.value.code == "SKILL_ID_RESERVED"
