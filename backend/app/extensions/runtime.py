from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.agent.permissions import KNOWN_PERMISSIONS
from app.contracts import (
    ModelCapability,
    Plugin,
    PluginManifest,
    PluginStatus,
    RetrievalConfig,
    Skill,
    SkillManifest,
    SkillStatus,
    ToolDefinition,
)

_EXTENSION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class ExtensionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True, slots=True)
class AgentConfiguration:
    skill_id: str
    system_prompt: str
    allowed_tools: list[str]
    permissions: list[str]
    retrieval: RetrievalConfig


@dataclass(slots=True)
class _SkillRecord:
    skill: Skill
    prompt: str
    package_path: Path


class SkillRuntime:
    """声明式 Skill 生命周期；Skill 只生成 Agent 配置，不执行第三方代码。"""

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools
        self._records: dict[str, _SkillRecord] = {}

    def install(self, package_path: str | Path) -> Skill:
        root = _package_dir(package_path)
        raw = _read_yaml(root / "skill.yaml")
        if "id" in raw and "skill_id" not in raw:
            raw["skill_id"] = raw.pop("id")
        try:
            manifest = SkillManifest.model_validate(raw)
        except ValidationError as exc:
            raise _manifest_error("skill", exc) from exc
        _validate_id("skill", manifest.skill_id)
        _validate_permissions("skill", manifest.permissions)
        if manifest.skill_id in self._records:
            raise ExtensionError(
                "SKILL_ALREADY_INSTALLED",
                f"Skill is already installed: {manifest.skill_id}",
                status_code=409,
            )
        prompt_path = root / "prompt.md"
        prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
        record = _SkillRecord(
            skill=Skill(manifest=manifest, status=SkillStatus.installed),
            prompt=prompt,
            package_path=root,
        )
        self._records[manifest.skill_id] = record
        self._refresh(record)
        return record.skill.model_copy(deep=True)

    def list(self) -> list[Skill]:
        for record in self._records.values():
            self._refresh(record)
        return [record.skill.model_copy(deep=True) for record in self._records.values()]

    def get(self, skill_id: str) -> Skill:
        record = self._record(skill_id)
        self._refresh(record)
        return record.skill.model_copy(deep=True)

    def enable(self, skill_id: str) -> Skill:
        record = self._record(skill_id)
        missing = self._missing_tools(record.skill.manifest)
        if missing:
            record.skill.enabled = False
            record.skill.status = SkillStatus.dependency_missing
            record.skill.missing_dependencies = missing
            raise ExtensionError(
                "SKILL_DEPENDENCY_MISSING",
                f"Skill has missing tools: {', '.join(missing)}",
                status_code=409,
                details={"skill_id": skill_id, "missing_tools": missing},
            )
        undeclared = self._undeclared_permissions(record.skill.manifest)
        if undeclared:
            record.skill.enabled = False
            record.skill.status = SkillStatus.permission_required
            raise ExtensionError(
                "SKILL_PERMISSION_UNDECLARED",
                "Skill tools require permissions missing from the manifest.",
                status_code=409,
                details={"skill_id": skill_id, "permissions": undeclared},
            )
        record.skill.enabled = True
        record.skill.status = SkillStatus.ready
        record.skill.missing_dependencies = []
        return record.skill.model_copy(deep=True)

    def disable(self, skill_id: str) -> Skill:
        record = self._record(skill_id)
        record.skill.enabled = False
        record.skill.status = SkillStatus.disabled
        return record.skill.model_copy(deep=True)

    def uninstall(self, skill_id: str) -> None:
        self._record(skill_id)
        del self._records[skill_id]

    def build_agent_configuration(
        self, skill_id: str, provider_capabilities: list[ModelCapability]
    ) -> AgentConfiguration:
        record = self._record(skill_id)
        self._refresh(record)
        if not record.skill.enabled or record.skill.status != SkillStatus.ready:
            raise ExtensionError(
                "SKILL_NOT_READY",
                f"Skill is not enabled and ready: {skill_id}",
                status_code=409,
            )
        required = set(record.skill.manifest.model.required_capabilities)
        missing_capabilities = sorted(cap.value for cap in required - set(provider_capabilities))
        if missing_capabilities:
            raise ExtensionError(
                "SKILL_MODEL_CAPABILITY_MISSING",
                "Provider does not satisfy the Skill model requirements.",
                status_code=409,
                details={"skill_id": skill_id, "missing_capabilities": missing_capabilities},
            )
        return AgentConfiguration(
            skill_id=skill_id,
            system_prompt=record.prompt,
            allowed_tools=list(record.skill.manifest.tools),
            permissions=list(record.skill.manifest.permissions),
            retrieval=record.skill.manifest.retrieval.model_copy(deep=True),
        )

    def depending_on_tools(self, names: list[str]) -> list[str]:
        target = set(names)
        return [
            skill_id
            for skill_id, record in self._records.items()
            if record.skill.enabled and target.intersection(record.skill.manifest.tools)
        ]

    def _record(self, skill_id: str) -> _SkillRecord:
        try:
            return self._records[skill_id]
        except KeyError as exc:
            raise ExtensionError(
                "SKILL_NOT_FOUND", f"Skill is not installed: {skill_id}", status_code=404
            ) from exc

    def _missing_tools(self, manifest: SkillManifest) -> list[str]:
        return [name for name in manifest.tools if not self.tools.contains(name)]

    def _undeclared_permissions(self, manifest: SkillManifest) -> list[str]:
        declared = set(manifest.permissions)
        required = {
            self.tools.get(name).definition.permission
            for name in manifest.tools
            if self.tools.contains(name) and self.tools.get(name).definition.permission
        }
        return sorted(required - declared)

    def _refresh(self, record: _SkillRecord) -> None:
        missing = self._missing_tools(record.skill.manifest)
        record.skill.missing_dependencies = missing
        if missing:
            record.skill.status = SkillStatus.dependency_missing
        elif self._undeclared_permissions(record.skill.manifest):
            record.skill.status = SkillStatus.permission_required
        elif record.skill.enabled:
            record.skill.status = SkillStatus.ready
        elif record.skill.status != SkillStatus.installed:
            record.skill.status = SkillStatus.disabled


class DeclarativeToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    permission: str | None = None
    handler: Literal["echo", "uppercase"]


class DeclarativePluginHost:
    """第一阶段内置 Host：仅执行宿主实现的白名单 handler，不加载插件代码。"""

    async def execute(
        self, handler: str, arguments: BaseModel, _: ToolExecutionContext
    ) -> Any:
        values = arguments.model_dump()
        if handler == "echo":
            return values
        if handler == "uppercase":
            return {"text": str(values.get("text", "")).upper()}
        raise ExtensionError("PLUGIN_HANDLER_UNSUPPORTED", f"Unsupported handler: {handler}")


@dataclass(slots=True)
class _PluginRecord:
    plugin: Plugin
    tools: list[DeclarativeToolSpec]
    package_path: Path
    registered_tools: list[str]


class PluginRuntime:
    """Plugin Manifest、生命周期及 Tool Contribution 注册。"""

    def __init__(self, tools: ToolRegistry, host: DeclarativePluginHost | None = None) -> None:
        self.registry = tools
        self.host = host or DeclarativePluginHost()
        self._records: dict[str, _PluginRecord] = {}

    def install(self, package_path: str | Path) -> Plugin:
        root = _package_dir(package_path)
        raw = _read_yaml(root / "plugin.yaml")
        if "id" in raw and "plugin_id" not in raw:
            raw["plugin_id"] = raw.pop("id")
        try:
            manifest = PluginManifest.model_validate(raw)
        except ValidationError as exc:
            raise _manifest_error("plugin", exc) from exc
        _validate_id("plugin", manifest.plugin_id)
        _validate_permissions("plugin", manifest.permissions)
        if manifest.plugin_id in self._records:
            raise ExtensionError(
                "PLUGIN_ALREADY_INSTALLED",
                f"Plugin is already installed: {manifest.plugin_id}",
                status_code=409,
            )

        specs = self._load_tools(root)
        declared = set(manifest.contributes.tools)
        actual = {spec.name for spec in specs}
        if declared != actual:
            raise ExtensionError(
                "PLUGIN_CONTRIBUTION_INVALID",
                "plugin.yaml tool contributions must exactly match tools.yaml",
                details={"declared": sorted(declared), "actual": sorted(actual)},
            )
        for spec in specs:
            _validate_id("tool", spec.name)
            _validate_tool_schema(spec)
            if spec.permission and spec.permission not in manifest.permissions:
                raise ExtensionError(
                    "PLUGIN_PERMISSION_UNDECLARED",
                    f"Tool permission is not declared by Plugin: {spec.permission}",
                    details={"tool": spec.name, "permission": spec.permission},
                )

        record = _PluginRecord(
            plugin=Plugin(
                manifest=manifest,
                status=(
                    PluginStatus.permission_required
                    if manifest.permissions
                    else PluginStatus.installed
                ),
            ),
            tools=specs,
            package_path=root,
            registered_tools=[],
        )
        self._records[manifest.plugin_id] = record
        return record.plugin.model_copy(deep=True)

    def list(self) -> list[Plugin]:
        return [record.plugin.model_copy(deep=True) for record in self._records.values()]

    def get(self, plugin_id: str) -> Plugin:
        return self._record(plugin_id).plugin.model_copy(deep=True)

    def enable(self, plugin_id: str) -> Plugin:
        record = self._record(plugin_id)
        if record.plugin.enabled:
            return record.plugin.model_copy(deep=True)
        if record.plugin.manifest.backend.type == "mcp":
            record.plugin.status = PluginStatus.dependency_missing
            raise ExtensionError(
                "PLUGIN_HOST_UNAVAILABLE",
                "MCP Plugin Host is reserved for the second development phase.",
                status_code=501,
                details={"plugin_id": plugin_id, "backend": "mcp"},
            )
        missing_grants = sorted(
            set(record.plugin.manifest.permissions) - set(record.plugin.granted_permissions)
        )
        if missing_grants:
            record.plugin.status = PluginStatus.permission_required
            raise ExtensionError(
                "PLUGIN_PERMISSION_REQUIRED",
                "Plugin permissions must be granted before it can be enabled.",
                status_code=409,
                details={"plugin_id": plugin_id, "permissions": missing_grants},
            )
        conflicts = [spec.name for spec in record.tools if self.registry.contains(spec.name)]
        if conflicts:
            raise ExtensionError(
                "PLUGIN_TOOL_CONFLICT",
                f"Plugin tools are already registered: {', '.join(conflicts)}",
                status_code=409,
                details={"plugin_id": plugin_id, "tools": conflicts},
            )
        record.plugin.status = PluginStatus.starting
        try:
            for spec in record.tools:
                arguments_model = _arguments_model(spec)

                async def executor(
                    arguments: BaseModel,
                    context: ToolExecutionContext,
                    _handler: str = spec.handler,
                ) -> Any:
                    return await self.host.execute(_handler, arguments, context)

                self.registry.register(
                    ToolDefinition(
                        name=spec.name,
                        description=spec.description,
                        parameters=spec.parameters,
                        permission=spec.permission,
                        source="plugin",
                    ),
                    arguments_model,
                    executor,
                )
                record.registered_tools.append(spec.name)
        except Exception as exc:
            for name in record.registered_tools:
                self.registry.unregister(name)
            record.registered_tools.clear()
            record.plugin.status = PluginStatus.error
            record.plugin.error_message = str(exc)
            raise
        record.plugin.enabled = True
        record.plugin.status = PluginStatus.ready
        record.plugin.error_message = None
        return record.plugin.model_copy(deep=True)

    def set_permissions(self, plugin_id: str, permissions: list[str]) -> Plugin:
        record = self._record(plugin_id)
        requested = set(permissions)
        declared = set(record.plugin.manifest.permissions)
        undeclared = sorted(requested - declared)
        if undeclared:
            raise ExtensionError(
                "PLUGIN_PERMISSION_UNDECLARED",
                "Cannot grant permissions that are not declared by the Plugin.",
                details={"plugin_id": plugin_id, "permissions": undeclared},
            )
        record.plugin.granted_permissions = sorted(requested)
        missing = declared - requested
        if missing and record.plugin.enabled:
            self.disable(plugin_id)
        if missing:
            record.plugin.status = PluginStatus.permission_required
        elif not record.plugin.enabled:
            record.plugin.status = PluginStatus.installed
        return record.plugin.model_copy(deep=True)

    def disable(self, plugin_id: str) -> Plugin:
        record = self._record(plugin_id)
        for name in record.registered_tools:
            self.registry.unregister(name)
        record.registered_tools.clear()
        record.plugin.enabled = False
        record.plugin.status = PluginStatus.disabled
        return record.plugin.model_copy(deep=True)

    def uninstall(self, plugin_id: str, dependent_skills: list[str] | None = None) -> None:
        record = self._record(plugin_id)
        if dependent_skills:
            raise ExtensionError(
                "PLUGIN_IN_USE",
                "Enabled Skills depend on this Plugin.",
                status_code=409,
                details={"plugin_id": plugin_id, "skills": dependent_skills},
            )
        if record.plugin.enabled:
            self.disable(plugin_id)
        del self._records[plugin_id]

    def _record(self, plugin_id: str) -> _PluginRecord:
        try:
            return self._records[plugin_id]
        except KeyError as exc:
            raise ExtensionError(
                "PLUGIN_NOT_FOUND", f"Plugin is not installed: {plugin_id}", status_code=404
            ) from exc

    @staticmethod
    def _load_tools(root: Path) -> list[DeclarativeToolSpec]:
        path = root / "tools.yaml"
        if not path.exists():
            return []
        raw = _read_yaml(path)
        try:
            return [DeclarativeToolSpec.model_validate(item) for item in raw.get("tools", [])]
        except ValidationError as exc:
            raise _manifest_error("plugin tool", exc) from exc


def _package_dir(package_path: str | Path) -> Path:
    root = Path(package_path).expanduser().resolve()
    if not root.is_dir():
        raise ExtensionError(
            "EXTENSION_PACKAGE_NOT_FOUND",
            f"Extension package directory does not exist: {root}",
            status_code=404,
        )
    return root


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ExtensionError(
            "EXTENSION_MANIFEST_NOT_FOUND", f"Manifest does not exist: {path}", status_code=404
        )
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ExtensionError("EXTENSION_MANIFEST_INVALID", f"Cannot read manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ExtensionError("EXTENSION_MANIFEST_INVALID", "Manifest root must be an object.")
    return value


def _validate_id(kind: str, value: str) -> None:
    if not _EXTENSION_ID.fullmatch(value):
        raise ExtensionError(
            "EXTENSION_ID_INVALID",
            f"Invalid {kind} id: {value}",
            details={"kind": kind, "id": value},
        )


def _validate_permissions(kind: str, permissions: list[str]) -> None:
    unknown = sorted(set(permissions) - KNOWN_PERMISSIONS)
    if unknown:
        raise ExtensionError(
            "EXTENSION_PERMISSION_INVALID",
            f"Invalid {kind} permissions: {', '.join(unknown)}",
            details={"kind": kind, "permissions": unknown},
        )


def _manifest_error(kind: str, exc: ValidationError) -> ExtensionError:
    return ExtensionError(
        "EXTENSION_MANIFEST_INVALID",
        f"Invalid {kind} manifest.",
        details={"errors": exc.errors(include_url=False)},
    )


def _arguments_model(spec: DeclarativeToolSpec) -> type[BaseModel]:
    schema = spec.parameters or {"type": "object", "properties": {}}
    if schema.get("type", "object") != "object":
        raise ExtensionError("PLUGIN_TOOL_SCHEMA_INVALID", "Tool parameters must be an object schema.")
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, tuple[Any, Any]] = {}
    types = {
        "string": str,
        "number": float,
        "integer": int,
        "boolean": bool,
        "array": list[Any],
        "object": dict[str, Any],
    }
    for name, field_schema in properties.items():
        annotation = types.get(field_schema.get("type"), Any)
        fields[name] = (annotation, ... if name in required else None)
    model_name = "PluginArgs_" + re.sub(r"\W+", "_", spec.name)
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


def _validate_tool_schema(spec: DeclarativeToolSpec) -> None:
    schema = spec.parameters or {"type": "object", "properties": {}}
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ExtensionError(
            "PLUGIN_TOOL_SCHEMA_INVALID",
            f"Invalid JSON Schema for tool {spec.name}: {exc.message}",
            details={"tool": spec.name},
        ) from exc
    if schema.get("type", "object") != "object" or not isinstance(
        schema.get("properties", {}), dict
    ):
        raise ExtensionError(
            "PLUGIN_TOOL_SCHEMA_INVALID",
            "Tool parameters must be an object schema with object properties.",
            details={"tool": spec.name},
        )
