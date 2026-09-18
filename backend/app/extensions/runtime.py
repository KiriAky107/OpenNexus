from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import (
    SchemaError,
    ValidationError as JsonSchemaValidationError,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    create_model,
)

from app.agent.tools import ToolExecutionContext, ToolExecutionError, ToolRegistry
from app.agent.permissions import KNOWN_PERMISSIONS
from app.contracts import (
    ModelCapability,
    Plugin,
    PluginCommand,
    PluginCommandContext,
    PluginCommandEffect,
    PluginNoEffect,
    PluginNotificationEffect,
    PluginCommandLocation,
    PluginCommandResult,
    PluginManifest,
    PluginHostStatus,
    PluginSecretStatus,
    PluginSettingType,
    PluginSettingsSchema,
    PluginStatus,
    RetrievalConfig,
    Skill,
    SkillManifest,
    SkillStatus,
    ToolDefinition,
)
from app.extensions.contributions import (
    CommandRegistry,
    PluginCommandSpec,
    PluginSecretResolver,
    PluginSettingsDefinition,
    PluginSettingsStore,
    validate_command_spec,
    validate_settings_definition,
)
from app.extensions.errors import ExtensionError
from app.extensions.mcp import McpBridge, McpBridgeError, McpDiscoveredTool
from app.providers.credentials import EncryptedCredentialStore
from app.schema_security import (
    SchemaReferenceError,
    reject_external_schema_references,
)

_EXTENSION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


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
        # 应用层 InstalledRuntime 负责安装记录和可信包恢复；此类保留独立可测试的运行时。
        root = _package_dir(package_path)
        raw = _read_yaml(root / "skill.yaml")
        if "id" in raw and "skill_id" not in raw:
            raw["skill_id"] = raw.pop("id")
        try:
            manifest = SkillManifest.model_validate(raw)
        except ValidationError as exc:
            raise _manifest_error("skill", exc) from exc
        _validate_id("skill", manifest.skill_id)
        if manifest.skill_id.startswith("user_skill_"):
            raise ExtensionError(
                "SKILL_ID_RESERVED",
                "The user_skill_ prefix is reserved for Vault-owned user Skills.",
                status_code=422,
            )
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
    handler: Literal["echo", "uppercase", "execution_policy", "inspect_markdown"]


class DeclarativePluginHost:
    """第一阶段内置 Host：仅执行宿主实现的白名单 handler，不加载插件代码。"""

    async def execute(
        self, handler: str, arguments: BaseModel, _: ToolExecutionContext
    ) -> Any:
        values = arguments.model_dump()
        if handler == "echo":
            return values
        if handler == "execution_policy":
            task = str(values.get('task','')).strip()
            steps = int(values.get('max_steps',10))
            if not task or len(task)>16000 or not 1<=steps<=10:
                raise ExtensionError('INVALID_EXECUTION_PLAN','Task or step budget is invalid')
            return {'task':task,'max_steps':steps,'allow_network':False,'token_budget':16000,
                    'steps':['读取用户指定资料与当前版本','使用允许工具执行必要操作','重新读取或查询状态核验结果'],
                    'requires_permission_policy':True,'completion_requires_verification':True}
        if handler == "uppercase":
            return {"text": str(values.get("text", "")).upper()}
        if handler == "inspect_markdown":
            text = str(values.get("text", ""))
            if len(text) > 100_000:
                raise ExtensionError(
                    "PLUGIN_ARGUMENT_INVALID", "Markdown text exceeds 100000 characters"
                )
            headings: list[dict[str, Any]] = []
            tasks: list[dict[str, Any]] = []
            issues: list[dict[str, Any]] = []
            seen: dict[str, int] = {}
            previous_level = 0
            fence_marker: str | None = None
            fence_line = 0
            for line_number, line in enumerate(text.splitlines(), start=1):
                stripped = line.lstrip()
                marker = stripped[:3]
                if marker in {"```", "~~~"}:
                    if fence_marker is None:
                        fence_marker, fence_line = marker, line_number
                    elif marker == fence_marker:
                        fence_marker = None
                    continue
                if fence_marker is not None:
                    continue
                task_match = re.match(r"^\s*[-*+]\s+\[([ xX])\]\s+(.*)$", line)
                if task_match:
                    tasks.append(
                        {
                            "line": line_number,
                            "completed": task_match.group(1).lower() == "x",
                            "text": task_match.group(2).strip(),
                        }
                    )
                heading_match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
                if not heading_match:
                    continue
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                headings.append({"line": line_number, "level": level, "title": title})
                if previous_level and level > previous_level + 1:
                    issues.append(
                        {
                            "line": line_number,
                            "type": "heading_level_jump",
                            "message": f"标题从 H{previous_level} 跳到 H{level}",
                        }
                    )
                normalized = title.casefold()
                if normalized in seen:
                    issues.append(
                        {
                            "line": line_number,
                            "type": "duplicate_heading",
                            "message": f"标题与第 {seen[normalized]} 行重复",
                        }
                    )
                else:
                    seen[normalized] = line_number
                previous_level = level
            if fence_marker is not None:
                issues.append(
                    {
                        "line": fence_line,
                        "type": "unclosed_code_fence",
                        "message": "代码围栏未闭合",
                    }
                )
            open_tasks = sum(not item["completed"] for item in tasks)
            return {
                "summary": {
                    "lines": len(text.splitlines()),
                    "characters": len(text),
                    "headings": len(headings),
                    "tasks": len(tasks),
                    "open_tasks": open_tasks,
                    "issues": len(issues),
                },
                "headings": headings[:200],
                "tasks": tasks[:200],
                "issues": issues[:200],
                "truncated": any(len(items) > 200 for items in (headings, tasks, issues)),
                "method": "line-based Markdown checks; line numbers refer to the supplied text",
            }
        raise ExtensionError("PLUGIN_HANDLER_UNSUPPORTED", f"Unsupported handler: {handler}")

    async def execute_command(
        self,
        handler: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        settings: dict[str, Any],
        resolve_secret: PluginSecretResolver,
    ) -> PluginCommandEffect:
        """执行宿主内置的白名单 Command handler，不导入 Plugin Python 代码。"""

        if handler == "echo":
            message = str(arguments.get("message", context.get("selection", "")))
            if not message:
                return PluginNoEffect()
            return PluginNotificationEffect(
                payload={"level": "info", "message": message},
            )
        if handler == "uppercase_selection":
            text = str(arguments.get("text", context.get("selection", "")))
            limit = int(settings.get("result_limit", 100))
            return PluginNotificationEffect(
                payload={"level": "success", "message": text[:limit].upper()},
            )
        raise ExtensionError(
            "PLUGIN_HANDLER_UNSUPPORTED", f"Unsupported command handler: {handler}"
        )


@dataclass(slots=True)
class _PluginRecord:
    plugin: Plugin
    tools: list[DeclarativeToolSpec]
    commands: list[PluginCommandSpec]
    settings_definition: PluginSettingsDefinition | None
    package_path: Path
    registered_tools: list[str]
    registered_commands: list[str]
    mcp_remote_names: dict[str, str]
    mcp_command_schemas: dict[str, dict[str, Any]]


class PluginRuntime:
    """Plugin Manifest、生命周期及 Tool Contribution 注册。"""

    def __init__(
        self,
        tools: ToolRegistry,
        host: DeclarativePluginHost | None = None,
        mcp_bridge: McpBridge | None = None,
        credentials: EncryptedCredentialStore | None = None,
        *,
        allow_unsandboxed_mcp: bool = False,
    ) -> None:
        self.registry = tools
        self.host = host or DeclarativePluginHost()
        self.mcp = mcp_bridge or McpBridge()
        self.commands = CommandRegistry()
        self.settings = PluginSettingsStore(credentials or EncryptedCredentialStore())
        self.allow_unsandboxed_mcp = allow_unsandboxed_mcp
        self._records: dict[str, _PluginRecord] = {}
        self._lock = threading.RLock()

    def install(self, package_path: str | Path) -> Plugin:
        # 安装阶段只读取清单；MCP 子进程必须在权限授予后的 enable 阶段启动。
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

        _validate_backend(manifest)
        specs = [] if manifest.backend.type == "mcp" else self._load_tools(root)
        command_specs = self._load_commands(root)
        settings_definition = self._load_settings(root)
        if manifest.backend.type != "mcp":
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
        declared_commands = set(manifest.contributes.commands)
        actual_commands = {spec.command_id for spec in command_specs}
        if (
            declared_commands != actual_commands
            or len(manifest.contributes.commands) != len(declared_commands)
            or len(command_specs) != len(actual_commands)
        ):
            raise ExtensionError(
                "PLUGIN_CONTRIBUTION_INVALID",
                "plugin.yaml command contributions must exactly match commands.yaml",
                details={
                    "declared": sorted(declared_commands),
                    "actual": sorted(actual_commands),
                },
            )
        for spec in command_specs:
            validate_command_spec(manifest.plugin_id, spec)
            if spec.permission and spec.permission not in manifest.permissions:
                raise ExtensionError(
                    "PLUGIN_PERMISSION_UNDECLARED",
                    f"Command permission is not declared by Plugin: {spec.permission}",
                    details={"command": spec.command_id, "permission": spec.permission},
                )
        declared_sections = set(manifest.contributes.settings_sections)
        actual_sections = (
            {settings_definition.section_id} if settings_definition is not None else set()
        )
        if (
            declared_sections != actual_sections
            or len(manifest.contributes.settings_sections) != len(declared_sections)
        ):
            raise ExtensionError(
                "PLUGIN_CONTRIBUTION_INVALID",
                "plugin.yaml settings contributions must exactly match settings.yaml",
                details={
                    "declared": sorted(declared_sections),
                    "actual": sorted(actual_sections),
                },
            )
        if settings_definition is not None:
            validate_settings_definition(manifest.plugin_id, settings_definition)
        secret_fields = (
            {
                field.key
                for field in settings_definition.fields
                if field.type == PluginSettingType.secret
            }
            if settings_definition is not None
            else set()
        )
        for spec in command_specs:
            unknown_secrets = sorted(set(spec.secrets) - secret_fields)
            if unknown_secrets:
                raise ExtensionError(
                    "PLUGIN_COMMAND_INVALID",
                    "Plugin command references undeclared Secret settings.",
                    details={
                        "command_id": spec.command_id,
                        "secrets": unknown_secrets,
                    },
                )
            if spec.secrets and "secrets.use" not in manifest.permissions:
                raise ExtensionError(
                    "PLUGIN_PERMISSION_UNDECLARED",
                    "Commands using Secret settings require the secrets.use permission.",
                    details={"command_id": spec.command_id},
                )
            if spec.mcp_tool is not None:
                _validate_id("MCP command target", spec.mcp_tool)
                if manifest.backend.type != "mcp" or not spec.mcp_tool.startswith(
                    f"{manifest.plugin_id}."
                ):
                    raise ExtensionError(
                        "PLUGIN_COMMAND_INVALID",
                        "MCP Command target must use the current Plugin namespace.",
                        details={"command_id": spec.command_id},
                    )
                if spec.mcp_tool in manifest.contributes.tools:
                    raise ExtensionError(
                        "PLUGIN_COMMAND_INVALID",
                        "MCP Command target cannot also be exposed as an Agent Tool.",
                        details={"command_id": spec.command_id},
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
            commands=command_specs,
            settings_definition=settings_definition,
            package_path=root,
            registered_tools=[],
            registered_commands=[],
            mcp_remote_names={},
            mcp_command_schemas={},
        )
        self._records[manifest.plugin_id] = record
        return record.plugin.model_copy(deep=True)

    def list(self) -> list[Plugin]:
        return [record.plugin.model_copy(deep=True) for record in self._records.values()]

    def get(self, plugin_id: str) -> Plugin:
        return self._record(plugin_id).plugin.model_copy(deep=True)

    def enable(self, plugin_id: str) -> Plugin:
        # Host 启动和 Tool 批量注册必须串行，避免并发 enable 产生重复进程或半注册状态。
        with self._lock:
            return self._enable(plugin_id)

    def _enable(self, plugin_id: str) -> Plugin:
        record = self._record(plugin_id)
        if record.plugin.enabled:
            return record.plugin.model_copy(deep=True)
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
        if (
            record.plugin.manifest.backend.type == "mcp"
            and not self.allow_unsandboxed_mcp
        ):
            raise ExtensionError(
                "MCP_TRUST_APPROVAL_REQUIRED",
                "Unsandboxed MCP Hosts are disabled outside development mode.",
                status_code=403,
                details={"plugin_id": plugin_id},
            )
        if record.settings_definition is not None:
            self.settings.runtime_values(plugin_id, record.settings_definition)
        declared_tools = list(record.plugin.manifest.contributes.tools)
        conflicts = [name for name in declared_tools if self.registry.contains(name)]
        if conflicts:
            raise ExtensionError(
                "PLUGIN_TOOL_CONFLICT",
                f"Plugin tools are already registered: {', '.join(conflicts)}",
                status_code=409,
                details={"plugin_id": plugin_id, "tools": conflicts},
            )
        command_conflicts = [
            spec.command_id for spec in record.commands if self.commands.contains(spec.command_id)
        ]
        if command_conflicts:
            raise ExtensionError(
                "PLUGIN_COMMAND_CONFLICT",
                "Plugin commands are already registered.",
                status_code=409,
                details={"plugin_id": plugin_id, "commands": command_conflicts},
            )
        record.plugin.status = PluginStatus.starting
        try:
            if record.plugin.manifest.backend.type == "mcp":
                discovered = self._start_mcp(record)
                actual = {item.definition.name for item in discovered}
                declared = set(declared_tools)
                command_targets = {
                    spec.mcp_tool for spec in record.commands if spec.mcp_tool is not None
                }
                expected = declared | command_targets
                if actual != expected:
                    raise ExtensionError(
                        "PLUGIN_CONTRIBUTION_INVALID",
                        "Discovered MCP tools must exactly match Tool and Command targets.",
                        details={"declared": sorted(expected), "actual": sorted(actual)},
                    )
                for item in discovered:
                    if item.definition.name in declared:
                        self._register_mcp_tool(record, item)
                    else:
                        record.mcp_remote_names[item.definition.name] = item.remote_name
                        record.mcp_command_schemas[item.definition.name] = (
                            item.definition.parameters
                        )
                        for spec in (
                            command
                            for command in record.commands
                            if command.mcp_tool == item.definition.name
                        ):
                            _validate_mcp_command_target_schema(
                                item.definition.parameters,
                                spec.command_id,
                            )
            else:
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
            for spec in record.commands:

                async def command_executor(
                    arguments: dict[str, Any],
                    context: dict[str, Any],
                    _spec: PluginCommandSpec = spec,
                    _record: _PluginRecord = record,
                ) -> PluginCommandEffect:
                    if (
                        not _record.plugin.enabled
                        or _record.plugin.status != PluginStatus.ready
                    ):
                        raise ExtensionError(
                            "PLUGIN_COMMAND_NOT_FOUND",
                            "Plugin command is not available while its Plugin is inactive.",
                            status_code=404,
                            details={"command_id": _spec.command_id},
                        )
                    settings = (
                        self.settings.runtime_values(
                            _record.plugin.manifest.plugin_id,
                            _record.settings_definition,
                        )
                        if _record.settings_definition is not None
                        else {}
                    )

                    def resolve_secret(key: str) -> str | None:
                        if key not in _spec.secrets:
                            raise ExtensionError(
                                "PLUGIN_SECRET_ACCESS_DENIED",
                                "Command cannot access an undeclared Plugin Secret.",
                                status_code=403,
                                details={
                                    "command_id": _spec.command_id,
                                    "key": key,
                                },
                            )
                        if "secrets.use" not in _record.plugin.granted_permissions:
                            raise ExtensionError(
                                "PLUGIN_SECRET_ACCESS_DENIED",
                                "Plugin no longer has permission to access Secret settings.",
                                status_code=403,
                                details={"command_id": _spec.command_id, "key": key},
                            )
                        if _record.settings_definition is None:
                            return None
                        value = self.settings.resolve_secret(
                            _record.plugin.manifest.plugin_id,
                            _record.settings_definition,
                            key,
                        )
                        field = next(
                            item
                            for item in _record.settings_definition.fields
                            if item.key == key
                        )
                        if field.required and value is None:
                            raise ExtensionError(
                                "PLUGIN_SECRET_REQUIRED",
                                "A required Plugin Secret has not been configured.",
                                status_code=409,
                                details={"command_id": _spec.command_id, "key": key},
                            )
                        return value

                    if _spec.mcp_tool is not None:
                        remote_name = _record.mcp_remote_names[_spec.mcp_tool]
                        secret_values = {
                            key: value
                            for key in _spec.secrets
                            if (value := resolve_secret(key)) is not None
                        }
                        envelope = _mcp_command_envelope(
                            _spec,
                            arguments=arguments,
                            context=context,
                            settings=settings,
                            secrets=secret_values,
                        )
                        _validate_mcp_command_envelope(
                            _record.mcp_command_schemas[_spec.mcp_tool],
                            envelope,
                            _spec.command_id,
                        )
                        try:
                            effect = await self.mcp.call_tool(
                                _record.plugin.manifest.plugin_id,
                                remote_name,
                                envelope,
                                request_id=f"command:{uuid4().hex}",
                            )
                        except ToolExecutionError as exc:
                            raise ExtensionError(
                                exc.code,
                                "MCP Command target execution failed.",
                                status_code=502,
                                details={"command_id": _spec.command_id},
                            ) from exc
                        try:
                            return TypeAdapter(PluginCommandEffect).validate_python(effect)
                        except ValidationError as exc:
                            raise ExtensionError(
                                "PLUGIN_COMMAND_RESULT_INVALID",
                                "MCP Command target returned an invalid effect.",
                                status_code=502,
                                details={"command_id": _spec.command_id},
                            ) from exc

                    return await self.host.execute_command(
                        _spec.handler,
                        arguments,
                        context,
                        settings,
                        resolve_secret,
                    )

                self.commands.register(plugin_id, spec, command_executor)
                record.registered_commands.append(spec.command_id)
        except Exception as exc:
            # 注册过程必须具备回滚语义，防止半启用插件污染全局工具表。
            for name in record.registered_tools:
                self.registry.unregister(name)
            record.registered_tools.clear()
            for command_id in record.registered_commands:
                self.commands.unregister(command_id)
            record.registered_commands.clear()
            record.mcp_remote_names.clear()
            record.mcp_command_schemas.clear()
            self.mcp.stop(plugin_id)
            record.plugin.status = PluginStatus.error
            record.plugin.error_message = _safe_extension_message(exc)
            if isinstance(exc, ExtensionError):
                raise
            if isinstance(exc, McpBridgeError):
                raise ExtensionError(
                    exc.code,
                    exc.message,
                    status_code=exc.status_code,
                    details={"plugin_id": plugin_id},
                ) from exc
            raise ExtensionError(
                "PLUGIN_HOST_START_FAILED",
                record.plugin.error_message,
                status_code=503,
                details={"plugin_id": plugin_id},
            ) from exc
        record.plugin.enabled = True
        record.plugin.status = PluginStatus.ready
        record.plugin.error_message = None
        return record.plugin.model_copy(deep=True)

    def set_permissions(self, plugin_id: str, permissions: list[str]) -> Plugin:
        with self._lock:
            return self._set_permissions(plugin_id, permissions)

    def _set_permissions(self, plugin_id: str, permissions: list[str]) -> Plugin:
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
        with self._lock:
            return self._disable(plugin_id)

    def _disable(self, plugin_id: str) -> Plugin:
        record = self._record(plugin_id)
        for name in record.registered_tools:
            self.registry.unregister(name)
        record.registered_tools.clear()
        for command_id in record.registered_commands:
            self.commands.unregister(command_id)
        record.registered_commands.clear()
        record.mcp_remote_names.clear()
        record.mcp_command_schemas.clear()
        if record.plugin.manifest.backend.type == "mcp":
            self.mcp.stop(plugin_id)
        record.plugin.enabled = False
        record.plugin.status = PluginStatus.disabled
        return record.plugin.model_copy(deep=True)

    def get_host_status(self, plugin_id: str) -> PluginHostStatus:
        record = self._record(plugin_id)
        return self.mcp.status(plugin_id, record.plugin.manifest.backend)

    def list_commands(
        self, location: PluginCommandLocation | None = None
    ) -> list[PluginCommand]:
        return self.commands.list(location)

    async def execute_command(
        self,
        command_id: str,
        arguments: dict[str, Any],
        context: PluginCommandContext,
    ) -> PluginCommandResult:
        return await self.commands.execute(command_id, arguments, context)

    def get_settings(self, plugin_id: str) -> PluginSettingsSchema:
        record = self._record(plugin_id)
        definition = self._settings_definition(record)
        return self.settings.get(plugin_id, definition)

    def update_settings(
        self, plugin_id: str, schema_version: int, values: dict[str, Any]
    ) -> PluginSettingsSchema:
        record = self._record(plugin_id)
        definition = self._settings_definition(record)
        return self.settings.update(plugin_id, definition, schema_version, values)

    def put_setting_secret(
        self, plugin_id: str, key: str, secret: str
    ) -> PluginSecretStatus:
        record = self._record(plugin_id)
        definition = self._settings_definition(record)
        return self.settings.put_secret(plugin_id, definition, key, secret)

    def delete_setting_secret(self, plugin_id: str, key: str) -> PluginSecretStatus:
        record = self._record(plugin_id)
        definition = self._settings_definition(record)
        return self.settings.delete_secret(plugin_id, definition, key)

    def restart_host(self, plugin_id: str) -> PluginHostStatus:
        with self._lock:
            return self._restart_host(plugin_id)

    def _restart_host(self, plugin_id: str) -> PluginHostStatus:
        record = self._record(plugin_id)
        if record.plugin.manifest.backend.type != "mcp":
            raise ExtensionError(
                "PLUGIN_HOST_UNAVAILABLE",
                "Plugin does not use an MCP Host.",
                status_code=409,
                details={"plugin_id": plugin_id},
            )
        if record.plugin.status in {
            PluginStatus.installed,
            PluginStatus.disabled,
            PluginStatus.permission_required,
        }:
            raise ExtensionError(
                "PLUGIN_HOST_UNAVAILABLE",
                "Disabled or inactive MCP Plugins must be started with Enable.",
                status_code=409,
                details={"plugin_id": plugin_id, "status": record.plugin.status.value},
            )
        for name in record.registered_tools:
            self.registry.unregister(name)
        record.registered_tools.clear()
        for command_id in record.registered_commands:
            self.commands.unregister(command_id)
        record.registered_commands.clear()
        record.mcp_remote_names.clear()
        record.mcp_command_schemas.clear()
        self.mcp.stop(plugin_id)
        record.plugin.enabled = False
        record.plugin.status = PluginStatus.installed
        record.plugin.error_message = None
        self.enable(plugin_id)
        return self.get_host_status(plugin_id)

    def shutdown(self) -> None:
        """关闭所有隔离 Host；用于 FastAPI lifespan 和测试清理。"""

        with self._lock:
            for plugin_id, record in list(self._records.items()):
                if record.plugin.manifest.backend.type == "mcp":
                    self.mcp.stop(plugin_id)

    def _start_mcp(self, record: _PluginRecord) -> list[McpDiscoveredTool]:
        manifest = record.plugin.manifest
        return self.mcp.start(
            manifest.plugin_id,
            manifest.backend,
            record.package_path,
            manifest.permissions,
            self._handle_mcp_unavailable,
        )

    def _register_mcp_tool(
        self, record: _PluginRecord, discovered: McpDiscoveredTool
    ) -> None:
        definition = discovered.definition
        arguments_model = _arguments_model_from_schema(
            definition.name, definition.parameters
        )
        plugin_id = record.plugin.manifest.plugin_id
        remote_name = discovered.remote_name

        async def executor(
            arguments: BaseModel,
            context: ToolExecutionContext,
        ) -> Any:
            return await self.mcp.call_tool(
                plugin_id,
                remote_name,
                # 省略的可选字段不能被补成 null；显式传入的 null 仍由
                # model_fields_set 保留并交给 MCP Server。
                arguments.model_dump(exclude_unset=True),
                request_id=context.tool_call_id or f"{context.run_id}:{definition.name}",
            )

        self.registry.register(definition, arguments_model, executor)
        record.registered_tools.append(definition.name)
        record.mcp_remote_names[definition.name] = remote_name

    def _handle_mcp_unavailable(self, plugin_id: str, message: str) -> None:
        with self._lock:
            record = self._records.get(plugin_id)
            if record is None:
                return
            for name in record.registered_tools:
                self.registry.unregister(name)
            record.registered_tools.clear()
            for command_id in record.registered_commands:
                self.commands.unregister(command_id)
            record.registered_commands.clear()
            record.mcp_remote_names.clear()
            record.mcp_command_schemas.clear()
            record.plugin.enabled = False
            record.plugin.status = PluginStatus.error
            record.plugin.error_message = message

    def uninstall(self, plugin_id: str, dependent_skills: list[str] | None = None) -> None:
        with self._lock:
            self._uninstall(plugin_id, dependent_skills)

    def _uninstall(
        self, plugin_id: str, dependent_skills: list[str] | None = None
    ) -> None:
        record = self._record(plugin_id)
        if dependent_skills:
            raise ExtensionError(
                "PLUGIN_IN_USE",
                "Enabled Skills depend on this Plugin.",
                status_code=409,
                details={"plugin_id": plugin_id, "skills": dependent_skills},
            )
        is_mcp = record.plugin.manifest.backend.type == "mcp"
        if record.plugin.enabled:
            self.disable(plugin_id)
        if is_mcp:
            # stop 只结束本次进程并保留状态供故障诊断；真正卸载时必须连同
            # 历史状态一起遗忘，避免同 ID 重装继承旧协商信息。
            self.mcp.remove(plugin_id)
        self.settings.remove_plugin(plugin_id)
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

    @staticmethod
    def _load_commands(root: Path) -> list[PluginCommandSpec]:
        path = root / "commands.yaml"
        if not path.exists():
            return []
        raw = _read_yaml(path)
        items = raw.get("commands", [])
        if not isinstance(items, list):
            raise ExtensionError(
                "EXTENSION_MANIFEST_INVALID",
                "Invalid plugin command manifest: commands must be an array.",
            )
        try:
            return [
                PluginCommandSpec.model_validate(item)
                for item in items
            ]
        except ValidationError as exc:
            raise _manifest_error("plugin command", exc) from exc

    @staticmethod
    def _load_settings(root: Path) -> PluginSettingsDefinition | None:
        path = root / "settings.yaml"
        if not path.exists():
            return None
        raw = _read_yaml(path)
        try:
            return PluginSettingsDefinition.model_validate(raw)
        except ValidationError as exc:
            raise ExtensionError(
                "PLUGIN_SETTINGS_SCHEMA_INVALID",
                "Invalid Plugin settings schema.",
                details={"errors": exc.errors(include_url=False)},
            ) from exc

    @staticmethod
    def _settings_definition(record: _PluginRecord) -> PluginSettingsDefinition:
        if record.settings_definition is None:
            raise ExtensionError(
                "PLUGIN_SETTINGS_NOT_FOUND",
                "Plugin does not contribute a Settings section.",
                status_code=404,
                details={"plugin_id": record.plugin.manifest.plugin_id},
            )
        return record.settings_definition


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
    return _arguments_model_from_schema(spec.name, schema)


def _mcp_command_envelope(
    spec: PluginCommandSpec,
    *,
    arguments: dict[str, Any],
    context: dict[str, Any],
    settings: dict[str, Any],
    secrets: dict[str, str],
) -> dict[str, Any]:
    return {
        "_notesagent": {
            "command_id": spec.command_id,
            "arguments": arguments,
            "context": context,
            "settings": settings,
            "secrets": secrets,
        }
    }


def _validate_mcp_command_envelope(
    schema: dict[str, Any],
    envelope: dict[str, Any],
    command_id: str,
) -> None:
    """执行前用目标 Tool Schema 校验包含真实业务数据的宿主信封。"""

    try:
        Draft202012Validator(schema).validate(envelope)
    except JsonSchemaValidationError as exc:
        raise ExtensionError(
            "PLUGIN_COMMAND_TARGET_SCHEMA_MISMATCH",
            "MCP Command envelope does not match the target inputSchema.",
            status_code=502,
            details={"command_id": command_id, "path": list(exc.path)},
        ) from exc


def _validate_mcp_command_target_schema(
    schema: dict[str, Any], command_id: str
) -> None:
    """启用时只检查稳定信封入口，避免用伪造业务值误判合法 Schema。"""

    properties = schema.get("properties")
    envelope_schema = (
        properties.get("_notesagent") if isinstance(properties, dict) else None
    )
    if not isinstance(envelope_schema, dict) or envelope_schema.get("type") != "object":
        raise ExtensionError(
            "PLUGIN_CONTRIBUTION_INVALID",
            "MCP Command target inputSchema must directly declare "
            "_notesagent with type object.",
            details={"command_id": command_id},
        )


def _arguments_model_from_schema(
    tool_name: str, schema: dict[str, Any]
) -> type[BaseModel]:
    if schema.get("type", "object") != "object":
        raise ExtensionError("PLUGIN_TOOL_SCHEMA_INVALID", "Tool parameters must be an object schema.")
    model_name = "PluginArgs_" + re.sub(r"\W+", "_", tool_name)
    # 完整 JSON Schema 已在 ToolRegistry 中先行校验。参数载体不重复声明字段，
    # 从而完整保留 model_dump、连字符键、联合类型和动态属性等合法 JSON 键值。
    return create_model(model_name, __config__=ConfigDict(extra="allow"))


def _validate_tool_schema(spec: DeclarativeToolSpec) -> None:
    schema = spec.parameters or {"type": "object", "properties": {}}
    try:
        Draft202012Validator.check_schema(schema)
        reject_external_schema_references(schema)
    except (SchemaReferenceError, SchemaError) as exc:
        message = exc.message if isinstance(exc, SchemaError) else str(exc)
        raise ExtensionError(
            "PLUGIN_TOOL_SCHEMA_INVALID",
            f"Invalid JSON Schema for tool {spec.name}: {message}",
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


def _validate_backend(manifest: PluginManifest) -> None:
    backend = manifest.backend
    if backend.type == "mcp":
        if backend.transport != "stdio":
            raise ExtensionError(
                "MCP_CAPABILITY_UNSUPPORTED",
                "Phase C MCP Plugins must use stdio transport.",
                status_code=501,
            )
        if not backend.command or not backend.command.strip():
            raise ExtensionError(
                "EXTENSION_MANIFEST_INVALID",
                "MCP stdio backend requires a command.",
            )
    elif backend.command is not None or backend.args:
        raise ExtensionError(
            "EXTENSION_MANIFEST_INVALID",
            "Only MCP stdio backends may declare command or args.",
        )


def _safe_extension_message(exc: Exception) -> str:
    if isinstance(exc, (ExtensionError, McpBridgeError)):
        return exc.message
    return f"Plugin Host operation failed: {type(exc).__name__}."
