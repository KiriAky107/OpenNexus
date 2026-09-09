"""Vault 拥有的用户 Skill 记录及其声明性 Agent 配置。"""
from __future__ import annotations

from time import time_ns
from uuid import UUID, uuid4

from app import host_bridge
from app.agent.permissions import KNOWN_PERMISSIONS
from app.contracts import ModelCapability, UserSkill, UserSkillData, UserSkillWriteRequest
from app.errors import ApiError
from app.extensions.runtime import AgentConfiguration
from app.services.desktop_notes import call


def _operation_id() -> str:
    return host_bridge.operation_id.get() or str(uuid4())


def _validate_skill_id(skill_id: str) -> None:
    if not (
        skill_id.startswith("user_skill_")
        and len(skill_id) == 43
        and all(char in "0123456789abcdef" for char in skill_id[11:])
    ):
        raise ApiError(422, "USER_SKILL_ID_INVALID", "用户 Skill 标识无效。")


def _validate_declarations(request: UserSkillWriteRequest) -> None:
    unknown = sorted(set(request.permissions) - KNOWN_PERMISSIONS)
    if unknown:
        raise ApiError(
            422,
            "USER_SKILL_PERMISSION_UNKNOWN",
            "用户 Skill 声明了未知权限。",
            {"permissions": unknown},
        )


def _state(data: UserSkillData, tools) -> tuple[str, list[str], list[str]]:
    missing = [name for name in data.tools if not tools.contains(name)]
    declared = set(data.permissions)
    required = {
        tools.get(name).definition.permission
        for name in data.tools
        if tools.contains(name) and tools.get(name).definition.permission
    }
    undeclared = sorted(permission for permission in required - declared if permission)
    status = "dependency_missing" if missing else "permission_required" if undeclared else "ready"
    return status, missing, undeclared


def _public(document: dict, tools) -> UserSkill:
    data = UserSkillData.model_validate(document["record"]["data"])
    status, missing, undeclared = _state(data, tools)
    return UserSkill(
        skill_id=document["record"]["id"],
        revision=document["hash"],
        data=data,
        status=status,
        missing_dependencies=missing,
        undeclared_permissions=undeclared,
    )


def _request_values(request: UserSkillWriteRequest) -> dict:
    return request.model_dump(exclude={"revision"}, mode="json")


def _replay(operation_id: str, skill_id: str, request: UserSkillWriteRequest | None, expected: str):
    receipt = call("user_skills.operation", operation_id=operation_id)
    if receipt is None:
        return None
    data = receipt.get("record", {}).get("data", {})
    requested = {} if request is None else _request_values(request)
    mismatched_fields = sorted(
        key for key, value in requested.items() if data.get(key) != value
    )
    matches = (
        receipt.get("record", {}).get("kind") == "user_skill"
        and receipt.get("record", {}).get("id") == skill_id
        and receipt.get("expected") == expected
        and receipt.get("deleted") is (request is None)
        and not mismatched_fields
    )
    if not matches:
        raise ApiError(
            409,
            "USER_SKILL_OPERATION_CONFLICT",
            "该幂等键已用于不同的用户 Skill 操作。",
            {
                "kind_matches": receipt.get("record", {}).get("kind") == "user_skill",
                "id_matches": receipt.get("record", {}).get("id") == skill_id,
                "expected_matches": receipt.get("expected") == expected,
                "operation_matches": receipt.get("deleted") is (request is None),
                "mismatched_fields": mismatched_fields,
            },
        )
    return receipt if request is not None else True


def list_user_skills(tools, *, limit: int, offset: int) -> tuple[list[UserSkill], int]:
    page = call("user_skills.list", offset=offset, limit=limit)
    return [_public(item, tools) for item in page["items"]], page["total"]


def get_user_skill(skill_id: str, tools) -> UserSkill:
    _validate_skill_id(skill_id)
    document = call("user_skills.get", id=skill_id)
    if document is None:
        raise ApiError(404, "USER_SKILL_NOT_FOUND", "用户 Skill 不存在。", {"skill_id": skill_id})
    return _public(document, tools)


def create_user_skill(request: UserSkillWriteRequest, tools) -> UserSkill:
    _validate_declarations(request)
    if request.revision:
        raise ApiError(422, "USER_SKILL_REVISION_INVALID", "新建用户 Skill 时 revision 必须为空。")
    operation_id = _operation_id()
    skill_id = f"user_skill_{UUID(operation_id).hex}"
    if replay := _replay(operation_id, skill_id, request, ""):
        return _public(replay, tools)
    now = time_ns() // 1_000_000
    data = UserSkillData(
        version=1,
        created_at_ms=now,
        updated_at_ms=now,
        **request.model_dump(exclude={"revision"}),
    )
    document = call(
        "user_skills.write",
        record={"schema": 1, "kind": "user_skill", "id": skill_id, "data": data.model_dump(mode="json")},
        expected="",
        operation_id=operation_id,
    )
    return _public(document, tools)


def update_user_skill(skill_id: str, request: UserSkillWriteRequest, tools) -> UserSkill:
    _validate_skill_id(skill_id)
    _validate_declarations(request)
    if not request.revision:
        raise ApiError(422, "USER_SKILL_REVISION_REQUIRED", "更新用户 Skill 需要当前 revision。")
    operation_id = _operation_id()
    if replay := _replay(operation_id, skill_id, request, request.revision):
        return _public(replay, tools)
    current = get_user_skill(skill_id, tools)
    data = UserSkillData(
        version=current.data.version + 1,
        created_at_ms=current.data.created_at_ms,
        updated_at_ms=max(time_ns() // 1_000_000, current.data.updated_at_ms),
        **request.model_dump(exclude={"revision"}),
    )
    try:
        document = call(
            "user_skills.write",
            record={"schema": 1, "kind": "user_skill", "id": skill_id, "data": data.model_dump(mode="json")},
            expected=request.revision,
            operation_id=operation_id,
        )
    except ApiError as error:
        if error.code == "REVISION_CONFLICT":
            raise ApiError(409, "USER_SKILL_REVISION_CONFLICT", "用户 Skill 已被其他设备修改，请重新加载。") from None
        raise
    return _public(document, tools)


def delete_user_skill(skill_id: str, revision: str) -> None:
    _validate_skill_id(skill_id)
    if len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
        raise ApiError(422, "USER_SKILL_REVISION_INVALID", "删除用户 Skill 需要当前 revision。")
    operation_id = _operation_id()
    if _replay(operation_id, skill_id, None, revision):
        return
    try:
        call("user_skills.delete", id=skill_id, expected=revision, operation_id=operation_id)
    except ApiError as error:
        if error.code == "REVISION_CONFLICT":
            raise ApiError(409, "USER_SKILL_REVISION_CONFLICT", "用户 Skill 已被其他设备修改，请重新加载。") from None
        raise


def build_agent_configuration(skill_id: str, provider_capabilities: list[ModelCapability], tools) -> AgentConfiguration:
    skill = get_user_skill(skill_id, tools)
    if skill.status != "ready":
        raise ApiError(
            409,
            "USER_SKILL_NOT_READY",
            "用户 Skill 的工具或权限声明尚未满足。",
            {
                "skill_id": skill_id,
                "missing_dependencies": skill.missing_dependencies,
                "undeclared_permissions": skill.undeclared_permissions,
            },
        )
    missing = sorted(
        capability.value
        for capability in set(skill.data.required_capabilities) - set(provider_capabilities)
    )
    if missing:
        raise ApiError(
            409,
            "USER_SKILL_MODEL_CAPABILITY_MISSING",
            "当前模型不满足用户 Skill 的能力要求。",
            {"skill_id": skill_id, "missing_capabilities": missing},
        )
    return AgentConfiguration(
        skill_id=skill_id,
        system_prompt=skill.data.prompt,
        allowed_tools=list(skill.data.tools),
        permissions=list(skill.data.permissions),
        retrieval=skill.data.retrieval.model_copy(deep=True),
    )
