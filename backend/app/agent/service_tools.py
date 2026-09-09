"""基于现有 OpenNexus 应用服务的 Agent 工具。本模块中的工具沿用原笔记工具的验证、权限与审计流程。Plugin 编写仅限 Host 提供的声明式处理器，不能写入或启动任意代码。"""

from __future__ import annotations

import json
import shutil
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agent.permissions import KNOWN_PERMISSIONS
from app.agent.tools import ToolExecutionContext, ToolExecutionError, ToolRegistry
from app.contracts import ModelCapability, RetrievalConfig, ToolDefinition, UserSkillWriteRequest
from app.extensions.errors import ExtensionError
from app.plot.parser import parse_source
from app.services import note_service, task_service, transcription_service, user_skills


class ServiceToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class NoteRenameArguments(ServiceToolArguments):
    note_id: str = Field(min_length=1)
    file_name: str = Field(min_length=1, max_length=255)


class NoteDeleteArguments(ServiceToolArguments):
    note_id: str = Field(min_length=1)


class TaskReadArguments(ServiceToolArguments):
    task_id: str = Field(min_length=1)


class TaskDeleteArguments(ServiceToolArguments):
    task_id: str = Field(min_length=1)


class TranscriptionStatusArguments(ServiceToolArguments):
    job_id: str = Field(min_length=1, max_length=128)


class FunctionPlotComposeArguments(ServiceToolArguments):
    expressions: list[str] = Field(min_length=1, max_length=16)
    domain: tuple[float, float] = (-10.0, 10.0)
    y_range: tuple[float, float] | None = None
    xlabel: str | None = Field(default=None, max_length=80)
    ylabel: str | None = Field(default=None, max_length=80)
    grid: bool = True

    @field_validator("expressions")
    @classmethod
    def validate_expressions(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 2000 for value in cleaned):
            raise ValueError("each expression must contain 1 to 2000 characters")
        return cleaned

    @model_validator(mode="after")
    def validate_ranges(self):
        for name, value in (("domain", self.domain), ("y_range", self.y_range)):
            if value is not None and (value[0] >= value[1] or max(abs(value[0]), abs(value[1])) > 1_000_000):
                raise ValueError(f"{name} must be an increasing finite range within ±1000000")
        return self


class SkillListArguments(ServiceToolArguments):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SkillWriteFields(ServiceToolArguments):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    prompt: str = Field(default="", max_length=64000)
    tools: list[str] = Field(default_factory=list, max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=32)
    retrieval_top_k: int = Field(default=10, ge=1, le=50)
    retrieval_rerank: bool = True
    retrieval_citation: bool = True
    required_capabilities: list[ModelCapability] = Field(default_factory=list, max_length=16)


class SkillCreateArguments(SkillWriteFields):
    pass


class SkillUpdateArguments(SkillWriteFields):
    skill_id: str = Field(pattern=r"^user_skill_[0-9a-f]{32}$")
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class PluginToolDraft(ServiceToolArguments):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    handler: Literal["echo", "uppercase"] = "echo"
    permission: str | None = None

    @field_validator("permission")
    @classmethod
    def validate_permission(cls, value: str | None) -> str | None:
        if value is not None and value not in KNOWN_PERMISSIONS:
            raise ValueError("unknown permission")
        return value


class PluginCreateArguments(ServiceToolArguments):
    plugin_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=80)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1.0.0", pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
    description: str = Field(default="", max_length=2000)
    tools: list[PluginToolDraft] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def validate_tools(self):
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("plugin tool names must be unique")
        prefix = f"{self.plugin_id}."
        if any(not name.startswith(prefix) for name in names):
            raise ValueError(f"plugin tool names must start with {prefix}")
        return self


class PluginListArguments(ServiceToolArguments):
    pass


async def compose_function_plot(arguments: FunctionPlotComposeArguments, _: ToolExecutionContext) -> dict:
    lines = [f"domain: {arguments.domain[0]:g}, {arguments.domain[1]:g}"]
    if arguments.y_range is not None:
        lines.append(f"range: {arguments.y_range[0]:g}, {arguments.y_range[1]:g}")
    if arguments.xlabel:
        lines.append(f"xlabel: {arguments.xlabel}")
    if arguments.ylabel:
        lines.append(f"ylabel: {arguments.ylabel}")
    lines.append(f"grid: {'true' if arguments.grid else 'false'}")
    lines.extend(f"y = {expression}" for expression in arguments.expressions)
    source = "\n".join(lines)
    parsed = parse_source(source)
    if parsed.plot is None:
        message = "; ".join(item.message for item in parsed.diagnostics) or "Function Plot validation failed"
        raise ToolExecutionError("FUNCTION_PLOT_INVALID", message)
    return {
        "markdown": f"```function-plot\n{source}\n```",
        "source": source,
        "expression_count": len(parsed.plot.expressions),
        "node_count": parsed.plot.node_count,
        "diagnostics": [item.model_dump(mode="json") for item in parsed.diagnostics],
        "persisted": False,
    }


def _skill_request(arguments: SkillWriteFields, revision: str = "") -> UserSkillWriteRequest:
    return UserSkillWriteRequest(
        revision=revision,
        name=arguments.name,
        description=arguments.description,
        prompt=arguments.prompt,
        tools=arguments.tools,
        permissions=arguments.permissions,
        retrieval=RetrievalConfig(
            top_k=arguments.retrieval_top_k,
            rerank=arguments.retrieval_rerank,
            citation=arguments.retrieval_citation,
        ),
        required_capabilities=arguments.required_capabilities,
    )


def _register(registry: ToolRegistry, name: str, description: str, model: type[BaseModel], executor, permission: str | None = None) -> None:
    registry.register(
        ToolDefinition(name=name, description=description, parameters=model.model_json_schema(), permission=permission),
        model,
        executor,
    )


def register_service_tools(registry: ToolRegistry, plugins) -> None:
    """注册需要完整的Plugin运行时或当前注册表的工具。"""

    async def rename_note(arguments: NoteRenameArguments, _: ToolExecutionContext) -> dict:
        return (await note_service.rename_note(arguments.note_id, file_name=arguments.file_name)).model_dump(mode="json")

    async def delete_note(arguments: NoteDeleteArguments, _: ToolExecutionContext) -> dict:
        return {"deleted": await note_service.delete_note(arguments.note_id), "note_id": arguments.note_id}

    def read_task(arguments: TaskReadArguments, _: ToolExecutionContext) -> dict:
        task = task_service.get_task(arguments.task_id)
        if task is None:
            raise LookupError(f"Task does not exist: {arguments.task_id}")
        return task.model_dump(mode="json")

    def delete_task(arguments: TaskDeleteArguments, _: ToolExecutionContext) -> dict:
        return {"deleted": task_service.delete_task(arguments.task_id), "task_id": arguments.task_id}

    def transcription_status(arguments: TranscriptionStatusArguments, _: ToolExecutionContext) -> dict:
        return transcription_service.require_job(arguments.job_id).model_dump(mode="json")

    def list_skills(arguments: SkillListArguments, _: ToolExecutionContext) -> dict:
        items, total = user_skills.list_user_skills(registry, limit=arguments.limit, offset=arguments.offset)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "page": {"total": total, "limit": arguments.limit, "offset": arguments.offset},
            "scope": "current_vault",
        }

    def create_skill(arguments: SkillCreateArguments, _: ToolExecutionContext) -> dict:
        return user_skills.create_user_skill(_skill_request(arguments), registry).model_dump(mode="json")

    def update_skill(arguments: SkillUpdateArguments, _: ToolExecutionContext) -> dict:
        return user_skills.update_user_skill(
            arguments.skill_id, _skill_request(arguments, arguments.revision), registry
        ).model_dump(mode="json")

    def list_plugins(_: PluginListArguments, __: ToolExecutionContext) -> dict:
        return {"items": [item.model_dump(mode="json") for item in plugins.list()]}

    def create_plugin(arguments: PluginCreateArguments, context: ToolExecutionContext) -> dict:
        operation = context.tool_call_id or context.run_id
        safe_operation = "".join(char for char in operation.lower() if char in "0123456789abcdef")[:32] or "agent"
        root = (plugins.storage / f"agent-{safe_operation}-{arguments.plugin_id}").resolve()
        if root.parent != plugins.storage.resolve():
            raise ToolExecutionError("PLUGIN_PATH_INVALID", "Managed Plugin path is invalid")
        try:
            current = plugins.get(arguments.plugin_id)
        except ExtensionError as error:
            if error.code != "PLUGIN_NOT_FOUND":
                raise
            current = None
        if current is not None:
            record = plugins.runtime._record(arguments.plugin_id)
            if record.package_path.resolve() == root:
                return {**current.model_dump(mode="json"), "created": False, "requires_enable": not current.enabled}
            raise ToolExecutionError("PLUGIN_ALREADY_EXISTS", f"Plugin already exists: {arguments.plugin_id}")

        permissions = sorted({tool.permission for tool in arguments.tools if tool.permission})
        manifest = {
            "id": arguments.plugin_id,
            "name": arguments.name,
            "version": arguments.version,
            "description": arguments.description,
            "permissions": permissions,
            "contributes": {"tools": [tool.name for tool in arguments.tools]},
            "backend": {"type": "internal_rpc", "transport": "none"},
        }
        tool_specs = []
        for tool in arguments.tools:
            spec = {
                "name": tool.name,
                "description": tool.description,
                "handler": tool.handler,
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"text": {"type": "string", "maxLength": 16000}},
                    "required": ["text"],
                },
            }
            if tool.permission:
                spec["permission"] = tool.permission
            tool_specs.append(spec)

        if root.exists():
            marker = root / ".opennexus-agent-plugin.json"
            if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")).get("plugin_id") != arguments.plugin_id:
                raise ToolExecutionError("PLUGIN_PATH_CONFLICT", "Managed Plugin directory already exists")
        else:
            root.mkdir(parents=True)
        try:
            (root / "plugin.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (root / "tools.yaml").write_text(yaml.safe_dump({"tools": tool_specs}, allow_unicode=True, sort_keys=False), encoding="utf-8")
            (root / ".opennexus-agent-plugin.json").write_text(
                json.dumps({"plugin_id": arguments.plugin_id, "operation": operation}, ensure_ascii=False), encoding="utf-8"
            )
            plugin = plugins.install(root, managed_root=root)
        except Exception:
            if root.exists():
                shutil.rmtree(root)
            raise
        return {
            **plugin.model_dump(mode="json"),
            "created": True,
            "requires_enable": True,
            "package_path": str(root),
            "safety_profile": "declarative-host-handlers-only",
        }

    _register(registry, "function_plot.compose", "Create and validate a safe function-plot Markdown block from mathematical expressions.", FunctionPlotComposeArguments, compose_function_plot)
    _register(registry, "notes.rename", "Rename a note file while preserving its note ID and indexed blocks.", NoteRenameArguments, rename_note, "notes.write")
    _register(registry, "notes.delete", "Delete a note from the current Vault.", NoteDeleteArguments, delete_note, "notes.delete")
    _register(registry, "tasks.read", "Read a persistent task by task ID.", TaskReadArguments, read_task, "tasks.read")
    _register(registry, "tasks.delete", "Delete a persistent task by task ID.", TaskDeleteArguments, delete_task, "tasks.write")
    _register(registry, "audio.transcription_status", "Read the current status and transcript of a transcription job.", TranscriptionStatusArguments, transcription_status, "attachments.read")
    _register(registry, "skills.list", "List Vault-owned custom Skills and their dependency state.", SkillListArguments, list_skills)
    _register(registry, "skills.create", "Create a declarative custom Skill in the current Vault.", SkillCreateArguments, create_skill, "skills.write")
    _register(registry, "skills.update", "Update a Vault-owned custom Skill using its current revision.", SkillUpdateArguments, update_skill, "skills.write")
    _register(registry, "plugins.list", "List installed Plugins and their lifecycle state.", PluginListArguments, list_plugins)
    _register(registry, "plugins.create", "Create and install a disabled declarative Plugin using safe host handlers; enabling remains a separate user action.", PluginCreateArguments, create_plugin, "plugins.write")
