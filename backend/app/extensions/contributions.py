"""Plugin Command Registry 与 Settings/Secret 命名空间存储。"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import re
import threading
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import get_settings
from app.contracts import (
    PluginCommand,
    PluginCommandContext,
    PluginCommandEffect,
    PluginCommandLocation,
    PluginCommandResult,
    PluginSecretState,
    PluginSecretStatus,
    PluginSettingField,
    PluginSettingType,
    PluginSettingsSchema,
)
from app.extensions.errors import ExtensionError
from app.providers.credentials import CredentialStoreError, EncryptedCredentialStore
from app.schema_security import (
    SchemaReferenceError,
    reject_external_schema_references,
)

_CONTRIBUTION_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_SETTING_KEY = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_HOST_ICONS = {"bolt", "document", "edit", "link", "refresh", "search", "setting"}
_WHEN_TOKENS = {
    "workspace.has_vault",
    "editor.has_note",
    "editor.has_selection",
}
_CONTEXT_KEYS = {"vault_id", "note_id", "file_path", "selection"}
_WHEN_CONTEXT = {
    "workspace.has_vault": "vault_id",
    "editor.has_note": "note_id",
    "editor.has_selection": "selection",
}


class PluginCommandSpec(BaseModel):
    """包内 commands.yaml 的宿主侧声明，不直接暴露 handler。"""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    title: str
    description: str = ""
    icon: str | None = None
    locations: list[PluginCommandLocation] = Field(default_factory=list)
    when: list[str] = Field(default_factory=list)
    context: list[Literal["vault_id", "note_id", "file_path", "selection"]] = Field(
        default_factory=list
    )
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )
    permission: str | None = None
    secrets: list[str] = Field(default_factory=list)
    handler: Literal["echo", "uppercase_selection"] | None = None
    mcp_tool: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)

    @model_validator(mode="after")
    def validate_execution_target(self) -> "PluginCommandSpec":
        if (self.handler is None) == (self.mcp_tool is None):
            raise ValueError("Command must declare exactly one handler or mcp_tool target.")
        return self


CommandExecutor = Callable[
    [dict[str, Any], dict[str, Any]],
    PluginCommandEffect | Awaitable[PluginCommandEffect],
]
PluginSecretResolver = Callable[[str], str | None]


@dataclass(slots=True)
class _RegisteredCommand:
    command: PluginCommand
    spec: PluginCommandSpec
    executor: CommandExecutor


@dataclass(frozen=True, slots=True)
class PluginCommandAuditEvent:
    """不记录参数与上下文的轻量审计事件，避免把正文或 Secret 写入日志。"""

    command_id: str
    plugin_id: str
    status: Literal["completed", "failed"]
    duration_ms: int
    error_code: str | None
    created_at: datetime


class CommandRegistry:
    """只发布已启用 Plugin 的受控 Command Contribution。"""

    def __init__(self) -> None:
        self._commands: dict[str, _RegisteredCommand] = {}
        self._audit: deque[PluginCommandAuditEvent] = deque(maxlen=500)
        self._lock = threading.RLock()

    def register(
        self,
        plugin_id: str,
        spec: PluginCommandSpec,
        executor: CommandExecutor,
    ) -> None:
        validate_command_spec(plugin_id, spec)
        command = PluginCommand(
            command_id=spec.command_id,
            plugin_id=plugin_id,
            title=spec.title,
            description=spec.description,
            icon=spec.icon,
            locations=spec.locations,
            when=spec.when,
            parameters=spec.parameters,
            enabled=True,
        )
        with self._lock:
            if spec.command_id in self._commands:
                raise ExtensionError(
                    "PLUGIN_COMMAND_CONFLICT",
                    f"Plugin command is already registered: {spec.command_id}",
                    status_code=409,
                    details={"command_id": spec.command_id},
                )
            self._commands[spec.command_id] = _RegisteredCommand(command, spec, executor)

    def unregister(self, command_id: str) -> None:
        with self._lock:
            self._commands.pop(command_id, None)

    def contains(self, command_id: str) -> bool:
        with self._lock:
            return command_id in self._commands

    def list(self, location: PluginCommandLocation | None = None) -> list[PluginCommand]:
        with self._lock:
            items = [
                item.command.model_copy(deep=True)
                for item in self._commands.values()
                if location is None or location in item.command.locations
            ]
        return sorted(items, key=lambda item: item.command_id)

    def audit_events(self) -> list[PluginCommandAuditEvent]:
        """返回有界审计快照；事件刻意不包含 arguments/context/effect。"""

        with self._lock:
            return list(self._audit)

    async def execute(
        self,
        command_id: str,
        arguments: dict[str, Any],
        context: PluginCommandContext,
    ) -> PluginCommandResult:
        with self._lock:
            registered = self._commands.get(command_id)
        if registered is None:
            raise ExtensionError(
                "PLUGIN_COMMAND_NOT_FOUND",
                f"Plugin command is not registered or enabled: {command_id}",
                status_code=404,
                details={"command_id": command_id},
            )
        started_at = perf_counter()
        try:
            Draft202012Validator(registered.spec.parameters).validate(arguments)
        except JsonSchemaValidationError as exc:
            error = ExtensionError(
                "PLUGIN_COMMAND_ARGUMENT_INVALID",
                "Plugin command arguments do not match the declared schema.",
                details={"command_id": command_id, "path": list(exc.path)},
            )
            self._record_audit(registered, started_at, error.code)
            raise error from exc

        raw_context = context.model_dump(exclude_none=True)
        missing = [
            token
            for token in registered.spec.when
            if not raw_context.get(_WHEN_CONTEXT[token])
        ]
        if missing:
            error = ExtensionError(
                "PLUGIN_COMMAND_CONTEXT_INVALID",
                "Plugin command context does not satisfy its when conditions.",
                details={"command_id": command_id, "missing": missing},
            )
            self._record_audit(registered, started_at, error.code)
            raise error
        scoped_context = {
            key: raw_context[key]
            for key in registered.spec.context
            if key in raw_context
        }
        try:
            effect = registered.executor(dict(arguments), scoped_context)
            if inspect.isawaitable(effect):
                effect = await asyncio.wait_for(
                    effect, timeout=registered.spec.timeout_seconds
                )
        except TimeoutError as exc:
            error = ExtensionError(
                "PLUGIN_COMMAND_TIMEOUT",
                "Plugin command execution timed out.",
                status_code=504,
                details={"command_id": command_id},
            )
            self._record_audit(registered, started_at, error.code)
            raise error from exc
        except ExtensionError as exc:
            self._record_audit(registered, started_at, exc.code)
            raise
        except Exception as exc:
            error = ExtensionError(
                "PLUGIN_COMMAND_EXECUTION_FAILED",
                "Plugin command execution failed.",
                status_code=502,
                details={"command_id": command_id},
            )
            self._record_audit(registered, started_at, error.code)
            raise error from exc
        if not isinstance(effect, PluginCommandEffect):
            error = ExtensionError(
                "PLUGIN_COMMAND_RESULT_INVALID",
                "Plugin command returned an invalid effect.",
                status_code=502,
                details={"command_id": command_id},
            )
            self._record_audit(registered, started_at, error.code)
            raise error
        try:
            encoded_effect = json.dumps(effect.model_dump(mode="json"), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            error = ExtensionError(
                "PLUGIN_COMMAND_RESULT_INVALID",
                "Plugin command returned a non-serializable effect.",
                status_code=502,
                details={"command_id": command_id},
            )
            self._record_audit(registered, started_at, error.code)
            raise error from exc
        if len(encoded_effect.encode("utf-8")) > 64 * 1024:
            error = ExtensionError(
                "PLUGIN_COMMAND_RESULT_TOO_LARGE",
                "Plugin command effect exceeds the 64 KiB response limit.",
                status_code=502,
                details={"command_id": command_id},
            )
            self._record_audit(registered, started_at, error.code)
            raise error
        self._record_audit(registered, started_at, None)
        return PluginCommandResult(command_id=command_id, effect=effect)

    def _record_audit(
        self,
        registered: _RegisteredCommand,
        started_at: float,
        error_code: str | None,
    ) -> None:
        event = PluginCommandAuditEvent(
            command_id=registered.command.command_id,
            plugin_id=registered.command.plugin_id,
            status="failed" if error_code else "completed",
            duration_ms=max(0, round((perf_counter() - started_at) * 1000)),
            error_code=error_code,
            created_at=datetime.now(UTC),
        )
        with self._lock:
            self._audit.append(event)


class PluginSettingsDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str
    schema_version: int = Field(ge=1)
    fields: list[PluginSettingField] = Field(default_factory=list)


class PluginSettingsStore:
    """非敏感值写入插件命名空间；Secret 只保存加密凭据引用。"""

    def __init__(self, credentials: EncryptedCredentialStore) -> None:
        self.credentials = credentials
        self._lock = threading.RLock()

    @staticmethod
    def _path() -> Path:
        return get_settings().data_dir / "plugins" / "settings.json"

    def get(
        self, plugin_id: str, definition: PluginSettingsDefinition
    ) -> PluginSettingsSchema:
        with self._lock:
            entry = self._entry(self._read(), plugin_id)
            stored_values = entry.get("values", {})
            secret_refs = entry.get("secret_refs", {})
            if not isinstance(stored_values, dict) or not isinstance(secret_refs, dict):
                raise self._storage_format_error(plugin_id)
            values = {
                field.key: field.default
                for field in definition.fields
                if field.type != PluginSettingType.secret and field.default is not None
            }
            allowed_values = {
                field.key
                for field in definition.fields
                if field.type != PluginSettingType.secret
            }
            fields = {field.key: field for field in definition.fields}
            for key, value in stored_values.items():
                if key not in allowed_values:
                    continue
                try:
                    _validate_setting_value(fields[key], value)
                except ExtensionError as exc:
                    raise self._storage_format_error(plugin_id) from exc
                values[key] = value
            secrets: dict[str, PluginSecretState] = {}
            for field in definition.fields:
                if field.type != PluginSettingType.secret:
                    continue
                reference = secret_refs.get(field.key)
                secrets[field.key] = PluginSecretState(
                    configured=isinstance(reference, str) and self._has_secret(reference)
                )
            return PluginSettingsSchema(
                plugin_id=plugin_id,
                schema_version=definition.schema_version,
                fields=definition.fields,
                values=values,
                secrets=secrets,
            )

    def update(
        self,
        plugin_id: str,
        definition: PluginSettingsDefinition,
        schema_version: int,
        values: dict[str, Any],
    ) -> PluginSettingsSchema:
        if schema_version != definition.schema_version:
            raise ExtensionError(
                "PLUGIN_SETTINGS_VERSION_CONFLICT",
                "Plugin settings schema version is out of date.",
                status_code=409,
                details={
                    "plugin_id": plugin_id,
                    "requested_version": schema_version,
                    "current_version": definition.schema_version,
                },
            )
        fields = {field.key: field for field in definition.fields}
        unknown = sorted(set(values) - set(fields))
        if unknown:
            raise ExtensionError(
                "PLUGIN_SETTINGS_FIELD_INVALID",
                "Plugin settings contain unknown fields.",
                details={"plugin_id": plugin_id, "fields": unknown},
            )
        secret_keys = sorted(
            key for key in values if fields[key].type == PluginSettingType.secret
        )
        if secret_keys:
            raise ExtensionError(
                "PLUGIN_SETTINGS_FIELD_INVALID",
                "Secret fields must use the dedicated Secret endpoint.",
                details={"plugin_id": plugin_id, "fields": secret_keys},
            )
        for key, value in values.items():
            _validate_setting_value(fields[key], value)

        with self._lock:
            data = self._read()
            entry = self._entry(data, plugin_id, create=True)
            current = entry.get("values", {})
            if not isinstance(current, dict):
                raise self._storage_format_error(plugin_id)
            entry["values"] = current
            current.update(values)
            effective = {
                field.key: field.default
                for field in definition.fields
                if field.type != PluginSettingType.secret and field.default is not None
            }
            effective.update(current)
            missing = [
                field.key
                for field in definition.fields
                if field.required
                and field.type != PluginSettingType.secret
                and field.key not in effective
            ]
            if missing:
                raise ExtensionError(
                    "PLUGIN_SETTINGS_FIELD_INVALID",
                    "Required Plugin settings are missing.",
                    details={"plugin_id": plugin_id, "fields": missing},
                )
            entry["schema_version"] = definition.schema_version
            self._write(data)
        return self.get(plugin_id, definition)

    def put_secret(
        self,
        plugin_id: str,
        definition: PluginSettingsDefinition,
        key: str,
        secret: str,
    ) -> PluginSecretStatus:
        _secret_field(definition, plugin_id, key)
        if not secret:
            raise ExtensionError(
                "PLUGIN_SECRET_VALUE_INVALID",
                "Plugin secret cannot be empty.",
                details={"plugin_id": plugin_id, "key": key},
            )
        if len(secret.encode("utf-8")) > 64 * 1024:
            raise ExtensionError(
                "PLUGIN_SECRET_VALUE_INVALID",
                "Plugin secret exceeds the 64 KiB limit.",
                details={"plugin_id": plugin_id, "key": key},
            )
        reference = _secret_reference(plugin_id, key)
        with self._lock:
            data = self._read()
            entry = self._entry(data, plugin_id, create=True)
            refs = entry.get("secret_refs", {})
            if not isinstance(refs, dict):
                raise self._storage_format_error(plugin_id)
            entry["secret_refs"] = refs
            try:
                previous = self.credentials.resolve(reference)
                self.credentials.put(reference, secret)
            except CredentialStoreError as exc:
                raise ExtensionError(
                    "PLUGIN_SECRET_STORE_ERROR", str(exc), status_code=500
                ) from exc
            refs[key] = reference
            entry["schema_version"] = definition.schema_version
            try:
                self._write(data)
            except ExtensionError:
                # 普通设置落盘失败时恢复凭据旧值，避免产生不可达的新 Secret。
                try:
                    if previous is None:
                        self.credentials.delete(reference)
                    else:
                        self.credentials.put(reference, previous)
                except CredentialStoreError:
                    pass
                raise
        return PluginSecretStatus(plugin_id=plugin_id, key=key, configured=True)

    def delete_secret(
        self,
        plugin_id: str,
        definition: PluginSettingsDefinition,
        key: str,
    ) -> PluginSecretStatus:
        _secret_field(definition, plugin_id, key)
        with self._lock:
            data = self._read()
            entry = self._entry(data, plugin_id)
            refs = entry.get("secret_refs", {})
            if not isinstance(refs, dict):
                raise self._storage_format_error(plugin_id)
            reference = refs.pop(key, None)
            if reference:
                try:
                    self.credentials.delete(reference)
                except CredentialStoreError as exc:
                    raise ExtensionError(
                        "PLUGIN_SECRET_STORE_ERROR", str(exc), status_code=500
                    ) from exc
            if plugin_id in data:
                self._write(data)
        return PluginSecretStatus(plugin_id=plugin_id, key=key, configured=False)

    def resolve_secret(
        self, plugin_id: str, definition: PluginSettingsDefinition, key: str
    ) -> str | None:
        _secret_field(definition, plugin_id, key)
        with self._lock:
            entry = self._entry(self._read(), plugin_id)
            refs = entry.get("secret_refs", {})
            if not isinstance(refs, dict):
                raise self._storage_format_error(plugin_id)
            reference = refs.get(key)
        try:
            return self.credentials.resolve(reference) if isinstance(reference, str) else None
        except CredentialStoreError as exc:
            raise ExtensionError(
                "PLUGIN_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc

    def remove_plugin(self, plugin_id: str) -> None:
        with self._lock:
            data = self._read()
            entry = data.pop(plugin_id, None)
            if isinstance(entry, dict):
                refs = entry.get("secret_refs", {})
                if isinstance(refs, dict):
                    for reference in refs.values():
                        if isinstance(reference, str):
                            try:
                                self.credentials.delete(reference)
                            except CredentialStoreError as exc:
                                raise ExtensionError(
                                    "PLUGIN_SECRET_STORE_ERROR",
                                    str(exc),
                                    status_code=500,
                                ) from exc
            if entry is not None:
                self._write(data)

    def _has_secret(self, reference: str) -> bool:
        try:
            return self.credentials.has(reference)
        except CredentialStoreError as exc:
            raise ExtensionError(
                "PLUGIN_SECRET_STORE_ERROR", str(exc), status_code=500
            ) from exc

    @staticmethod
    def _storage_format_error(plugin_id: str) -> ExtensionError:
        return ExtensionError(
            "PLUGIN_STORAGE_ERROR",
            "Plugin settings namespace has an invalid format.",
            status_code=500,
            details={"plugin_id": plugin_id},
        )

    def _entry(
        self,
        data: dict[str, dict[str, Any]],
        plugin_id: str,
        *,
        create: bool = False,
    ) -> dict[str, Any]:
        entry = data.get(plugin_id)
        if entry is None:
            if create:
                data[plugin_id] = {}
                return data[plugin_id]
            return {}
        if not isinstance(entry, dict):
            raise self._storage_format_error(plugin_id)
        return entry

    def _read(self) -> dict[str, dict[str, Any]]:
        path = self._path()
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExtensionError(
                "PLUGIN_STORAGE_ERROR",
                "Plugin settings storage cannot be loaded.",
                status_code=500,
            ) from exc
        if not isinstance(value, dict):
            raise ExtensionError(
                "PLUGIN_STORAGE_ERROR",
                "Plugin settings storage has an invalid format.",
                status_code=500,
            )
        return value

    def _write(self, value: dict[str, dict[str, Any]]) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise ExtensionError(
                "PLUGIN_STORAGE_ERROR",
                "Plugin settings storage cannot be written.",
                status_code=500,
            ) from exc


def validate_settings_definition(
    plugin_id: str, definition: PluginSettingsDefinition
) -> None:
    if not _CONTRIBUTION_ID.fullmatch(definition.section_id):
        raise _settings_schema_error(plugin_id, "Settings section id is invalid.")
    if not definition.section_id.startswith(f"{plugin_id}."):
        raise _settings_schema_error(
            plugin_id, "Settings section id must use the Plugin namespace."
        )
    keys: set[str] = set()
    for field in definition.fields:
        if not _SETTING_KEY.fullmatch(field.key) or field.key in keys:
            raise _settings_schema_error(plugin_id, f"Invalid or duplicate setting key: {field.key}")
        keys.add(field.key)
        if field.type == PluginSettingType.select and not field.options:
            raise _settings_schema_error(plugin_id, f"Select setting requires options: {field.key}")
        if field.type != PluginSettingType.select and field.options:
            raise _settings_schema_error(plugin_id, f"Only select settings accept options: {field.key}")
        if field.type != PluginSettingType.number and (
            field.minimum is not None or field.maximum is not None
        ):
            raise _settings_schema_error(plugin_id, f"Only number settings accept bounds: {field.key}")
        if field.minimum is not None and field.maximum is not None and field.minimum > field.maximum:
            raise _settings_schema_error(plugin_id, f"Setting bounds are reversed: {field.key}")
        if field.type == PluginSettingType.secret and field.default is not None:
            raise _settings_schema_error(plugin_id, f"Secret settings cannot declare defaults: {field.key}")
        if field.default is not None:
            try:
                _validate_setting_value(field, field.default)
            except ExtensionError as exc:
                raise _settings_schema_error(plugin_id, exc.message) from exc


def validate_command_spec(plugin_id: str, spec: PluginCommandSpec) -> None:
    if not _CONTRIBUTION_ID.fullmatch(spec.command_id) or not spec.command_id.startswith(
        f"{plugin_id}."
    ):
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command id must be valid and use the Plugin namespace.",
            details={"plugin_id": plugin_id, "command_id": spec.command_id},
        )
    if not spec.locations:
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command must declare at least one location.",
            details={"command_id": spec.command_id},
        )
    if len(spec.locations) != len(set(spec.locations)):
        raise ExtensionError("PLUGIN_COMMAND_INVALID", "Plugin command locations must be unique.")
    if len(spec.when) != len(set(spec.when)) or len(spec.context) != len(set(spec.context)):
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command when/context entries must be unique.",
        )
    if len(spec.secrets) != len(set(spec.secrets)):
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command Secret entries must be unique.",
            details={"command_id": spec.command_id},
        )
    unknown_when = sorted(set(spec.when) - _WHEN_TOKENS)
    if unknown_when:
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command declares unsupported when tokens.",
            details={"command_id": spec.command_id, "when": unknown_when},
        )
    required_context = {_WHEN_CONTEXT[token] for token in spec.when}
    if not required_context.issubset(set(spec.context)):
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command context must include every field required by when.",
            details={"command_id": spec.command_id},
        )
    if not set(spec.context).issubset(_CONTEXT_KEYS):
        raise ExtensionError("PLUGIN_COMMAND_INVALID", "Plugin command context is invalid.")
    if spec.icon and spec.icon not in _HOST_ICONS:
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            "Plugin command icon is not a supported Host icon.",
            details={"command_id": spec.command_id, "icon": spec.icon},
        )
    if spec.parameters.get("type", "object") != "object":
        raise ExtensionError("PLUGIN_COMMAND_INVALID", "Command parameters must be an object schema.")
    try:
        reject_external_schema_references(spec.parameters)
        Draft202012Validator.check_schema(spec.parameters)
    except (SchemaReferenceError, SchemaError) as exc:
        message = exc.message if isinstance(exc, SchemaError) else str(exc)
        raise ExtensionError(
            "PLUGIN_COMMAND_INVALID",
            f"Plugin command parameters contain invalid JSON Schema: {message}",
        ) from exc


def _validate_setting_value(field: PluginSettingField, value: Any) -> None:
    valid = False
    if field.type == PluginSettingType.string:
        valid = isinstance(value, str) and len(value.encode("utf-8")) <= 64 * 1024
    elif field.type == PluginSettingType.number:
        valid = (
            (isinstance(value, int) and not isinstance(value, bool))
            or (isinstance(value, float) and math.isfinite(value))
        )
    elif field.type == PluginSettingType.boolean:
        valid = isinstance(value, bool)
    elif field.type == PluginSettingType.select:
        valid = isinstance(value, str) and value in field.options
    if not valid:
        raise ExtensionError(
            "PLUGIN_SETTINGS_FIELD_INVALID",
            f"Plugin setting has an invalid value: {field.key}",
            details={"key": field.key},
        )
    if field.type == PluginSettingType.number:
        if field.minimum is not None and value < field.minimum:
            raise ExtensionError(
                "PLUGIN_SETTINGS_FIELD_INVALID",
                f"Plugin setting is below its minimum: {field.key}",
                details={"key": field.key, "minimum": field.minimum},
            )
        if field.maximum is not None and value > field.maximum:
            raise ExtensionError(
                "PLUGIN_SETTINGS_FIELD_INVALID",
                f"Plugin setting is above its maximum: {field.key}",
                details={"key": field.key, "maximum": field.maximum},
            )


def _secret_field(
    definition: PluginSettingsDefinition, plugin_id: str, key: str
) -> PluginSettingField:
    field = next((item for item in definition.fields if item.key == key), None)
    if field is None or field.type != PluginSettingType.secret:
        raise ExtensionError(
            "PLUGIN_SECRET_FIELD_NOT_FOUND",
            f"Plugin secret field does not exist: {key}",
            status_code=404,
            details={"plugin_id": plugin_id, "key": key},
        )
    return field


def _secret_reference(plugin_id: str, key: str) -> str:
    digest = hashlib.sha256(f"{plugin_id}\0{key}".encode("utf-8")).hexdigest()
    return f"plugin.{digest}"


def _settings_schema_error(plugin_id: str, message: str) -> ExtensionError:
    return ExtensionError(
        "PLUGIN_SETTINGS_SCHEMA_INVALID",
        message,
        details={"plugin_id": plugin_id},
    )
