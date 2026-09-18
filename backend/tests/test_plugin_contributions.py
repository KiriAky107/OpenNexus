import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.agent import ToolRegistry
from app.config import BACKEND_DIR, get_settings
from app.container import build_container
from app.contracts import (
    PluginCommandContext,
    PluginCommandEffect,
    PluginNoEffect,
    PluginSettingType,
)
from app.extensions import ExtensionError, PluginRuntime
from app.extensions.contributions import _secret_reference
from app.extensions.runtime import DeclarativePluginHost
from app.providers.credentials import CredentialStoreError

TEXT_TOOLS = BACKEND_DIR / "extensions" / "plugins" / "text-tools"


def run(coroutine):
    return asyncio.run(coroutine)


def test_command_list_filter_and_lifecycle() -> None:
    container = build_container()

    commands = container.plugins.list_commands()
    palette = container.plugins.list_commands(location="command_palette")

    assert [item.command_id for item in commands] == ["text-tools.uppercase-selection"]
    assert palette[0].plugin_id == "text-tools"
    assert palette[0].icon == "edit"
    assert palette[0].when == ["editor.has_selection"]

    container.plugins.disable("text-tools")
    assert container.plugins.list_commands() == []
    with pytest.raises(ExtensionError) as exc:
        run(
            container.plugins.execute_command(
                "text-tools.uppercase-selection",
                {},
                PluginCommandContext(selection="hello"),
            )
        )
    assert exc.value.code == "PLUGIN_COMMAND_NOT_FOUND"

    container.plugins.enable("text-tools")
    assert len(container.plugins.list_commands()) == 1


def test_command_executes_with_scoped_context_and_settings() -> None:
    container = build_container()
    container.plugins.update_settings("text-tools", 1, {"result_limit": 4})

    result = run(
        container.plugins.execute_command(
            "text-tools.uppercase-selection",
            {},
            PluginCommandContext(
                vault_id="default",
                note_id="note_private",
                file_path="private.md",
                selection="abcdef",
            ),
        )
    )

    assert result.status == "completed"
    assert result.effect.type == "notification"
    assert result.effect.payload.model_dump() == {
        "level": "success",
        "message": "ABCD",
    }


def test_echo_command_returns_none_for_empty_message() -> None:
    host = DeclarativePluginHost()

    empty = run(host.execute_command("echo", {}, {}, {}, lambda _: None))
    populated = run(
        host.execute_command("echo", {"message": "hello"}, {}, {}, lambda _: None)
    )

    assert isinstance(empty, PluginNoEffect)
    assert populated.type == "notification"
    assert populated.payload.message == "hello"


def test_markdown_inspector_returns_line_based_findings() -> None:
    class Arguments(BaseModel):
        text: str

    markdown = "# 课程\n### 跳级\n## 重复\n## 重复\n- [ ] 复习\n```python\nprint(1)"
    result = run(
        DeclarativePluginHost().execute(
            "inspect_markdown", Arguments(text=markdown), None
        )
    )

    assert result["summary"] == {
        "lines": 7,
        "characters": len(markdown),
        "headings": 4,
        "tasks": 1,
        "open_tasks": 1,
        "issues": 3,
    }
    assert [issue["type"] for issue in result["issues"]] == [
        "heading_level_jump",
        "duplicate_heading",
        "unclosed_code_fence",
    ]
    assert [issue["line"] for issue in result["issues"]] == [2, 4, 6]


@pytest.mark.parametrize(
    ("effect_type", "payload"),
    [
        ("none", {"unexpected": True}),
        ("notification", {"level": "debug", "message": "invalid"}),
        ("navigate", {"route": "https://example.com"}),
        ("refresh", {"scope": "everything"}),
        ("job", {"job_id": "invalid job id"}),
    ],
)
def test_command_effect_rejects_untrusted_payloads(effect_type, payload) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(PluginCommandEffect).validate_python(
            {"type": effect_type, "payload": payload}
        )


def test_command_rejects_missing_context_and_invalid_arguments() -> None:
    container = build_container()

    with pytest.raises(ExtensionError) as context_error:
        run(
            container.plugins.execute_command(
                "text-tools.uppercase-selection", {}, PluginCommandContext()
            )
        )
    assert context_error.value.code == "PLUGIN_COMMAND_CONTEXT_INVALID"

    with pytest.raises(ExtensionError) as argument_error:
        run(
            container.plugins.execute_command(
                "text-tools.uppercase-selection",
                {"unknown": True},
                PluginCommandContext(selection="hello"),
            )
        )
    assert argument_error.value.code == "PLUGIN_COMMAND_ARGUMENT_INVALID"

    audit = container.plugins.commands.audit_events()
    assert [event.error_code for event in audit[-2:]] == [
        "PLUGIN_COMMAND_CONTEXT_INVALID",
        "PLUGIN_COMMAND_ARGUMENT_INVALID",
    ]
    # 审计事件不得携带参数、正文选区或返回 effect。
    assert "hello" not in repr(audit)


def test_command_only_receives_declared_context() -> None:
    class CapturingHost(DeclarativePluginHost):
        def __init__(self) -> None:
            self.context = None

        async def execute_command(
            self, handler, arguments, context, settings, resolve_secret
        ):
            self.context = context
            return PluginNoEffect()

    host = CapturingHost()
    runtime = PluginRuntime(ToolRegistry(), host=host)
    runtime.install(TEXT_TOOLS)
    runtime.enable("text-tools")

    run(
        runtime.execute_command(
            "text-tools.uppercase-selection",
            {},
            PluginCommandContext(
                vault_id="default", note_id="note_private", selection="visible"
            ),
        )
    )

    assert host.context == {"selection": "visible"}


def test_command_resolves_only_declared_plugin_secrets(tmp_path: Path) -> None:
    class SecretHost(DeclarativePluginHost):
        def __init__(self) -> None:
            self.secret = None
            self.denied_code = None

        async def execute_command(
            self, handler, arguments, context, settings, resolve_secret
        ):
            self.secret = resolve_secret("api_key")
            try:
                resolve_secret("undeclared")
            except ExtensionError as exc:
                self.denied_code = exc.code
            return PluginNoEffect()

    host = SecretHost()
    runtime = PluginRuntime(ToolRegistry(), host=host)
    package = tmp_path / "secret-command"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: secret-command
name: Secret Command
version: 1.0.0
permissions: [secrets.use]
contributes:
  commands: [secret-command.run]
  settings_sections: [secret-command.general]
backend:
  type: internal_rpc
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "commands.yaml").write_text(
        """
commands:
  - command_id: secret-command.run
    title: Secret Command
    locations: [command_palette]
    secrets: [api_key]
    handler: echo
""".strip(),
        encoding="utf-8",
    )
    (package / "settings.yaml").write_text(
        """
section_id: secret-command.general
schema_version: 1
fields:
  - key: api_key
    label: API Key
    type: secret
""".strip(),
        encoding="utf-8",
    )
    runtime.install(package)
    runtime.set_permissions("secret-command", ["secrets.use"])
    runtime.enable("secret-command")
    runtime.put_setting_secret("secret-command", "api_key", "runtime-only-secret")

    run(
        runtime.execute_command(
            "secret-command.run",
            {},
            PluginCommandContext(selection="visible"),
        )
    )

    assert host.secret == "runtime-only-secret"
    assert host.denied_code == "PLUGIN_SECRET_ACCESS_DENIED"
    assert "runtime-only-secret" not in repr(runtime.commands.audit_events())


def test_settings_schema_contains_defaults_and_hides_secret() -> None:
    container = build_container()

    schema = container.plugins.get_settings("text-tools")
    by_key = {field.key: field for field in schema.fields}

    assert schema.schema_version == 1
    assert schema.values == {
        "result_limit": 100,
        "label_prefix": "",
        "output_style": "notification",
        "enabled_hint": True,
    }
    assert "api_key" not in schema.values
    assert schema.secrets["api_key"].configured is False
    assert by_key["api_key"].type == PluginSettingType.secret


def test_settings_update_validates_version_type_bounds_and_secret_boundary() -> None:
    container = build_container()

    updated = container.plugins.update_settings(
        "text-tools", 1, {"result_limit": 20, "output_style": "compact"}
    )
    assert updated.values["result_limit"] == 20
    assert updated.values["output_style"] == "compact"

    cases = [
        (2, {}, "PLUGIN_SETTINGS_VERSION_CONFLICT"),
        (1, {"result_limit": 0}, "PLUGIN_SETTINGS_FIELD_INVALID"),
        (1, {"enabled_hint": "yes"}, "PLUGIN_SETTINGS_FIELD_INVALID"),
        (1, {"output_style": "unknown"}, "PLUGIN_SETTINGS_FIELD_INVALID"),
        (1, {"api_key": "plaintext"}, "PLUGIN_SETTINGS_FIELD_INVALID"),
        (1, {"unknown": True}, "PLUGIN_SETTINGS_FIELD_INVALID"),
    ]
    for version, values, code in cases:
        with pytest.raises(ExtensionError) as exc:
            container.plugins.update_settings("text-tools", version, values)
        assert exc.value.code == code


def test_required_plain_setting_blocks_enable_until_configured(tmp_path: Path) -> None:
    package = tmp_path / "required-setting"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: required-setting
name: Required Setting
version: 1.0.0
contributes:
  commands: [required-setting.run]
  settings_sections: [required-setting.general]
backend:
  type: internal_rpc
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "commands.yaml").write_text(
        """
commands:
  - command_id: required-setting.run
    title: Required Setting
    locations: [command_palette]
    handler: echo
""".strip(),
        encoding="utf-8",
    )
    (package / "settings.yaml").write_text(
        """
section_id: required-setting.general
schema_version: 1
fields:
  - key: endpoint
    label: Endpoint
    type: string
    required: true
""".strip(),
        encoding="utf-8",
    )
    runtime = PluginRuntime(ToolRegistry())
    runtime.install(package)

    with pytest.raises(ExtensionError) as exc:
        runtime.enable("required-setting")
    assert exc.value.code == "PLUGIN_SETTINGS_REQUIRED"
    assert runtime.get("required-setting").status == "installed"

    runtime.update_settings("required-setting", 1, {"endpoint": "local"})
    assert runtime.enable("required-setting").status == "ready"


def test_secret_roundtrip_never_enters_plain_settings_storage() -> None:
    container = build_container()
    plaintext = "stage-d-secret-value"

    status = container.plugins.put_setting_secret("text-tools", "api_key", plaintext)
    schema = container.plugins.get_settings("text-tools")
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    credentials_path = get_settings().data_dir / "credentials" / "credentials.json"

    assert status.configured is True
    assert schema.secrets["api_key"].configured is True
    assert "api_key" not in schema.values
    assert plaintext not in settings_path.read_text(encoding="utf-8")
    assert plaintext not in credentials_path.read_text(encoding="utf-8")
    stored_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    reference = stored_settings["text-tools"]["secret_refs"]["api_key"]
    assert reference.startswith("plugin.")
    assert len(reference) == 71
    assert "text-tools" not in reference and "api_key" not in reference
    assert container.credentials.resolve(reference) == plaintext

    deleted = container.plugins.delete_setting_secret("text-tools", "api_key")
    assert deleted.configured is False
    assert container.credentials.resolve(reference) is None


def test_uninstall_removes_plugin_settings_and_secret_namespace() -> None:
    container = build_container()
    container.plugins.update_settings("text-tools", 1, {"result_limit": 12})
    container.plugins.put_setting_secret("text-tools", "api_key", "temporary")
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    reference = json.loads(settings_path.read_text(encoding="utf-8"))[
        "text-tools"
    ]["secret_refs"]["api_key"]

    container.plugins.uninstall("text-tools")

    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "text-tools" not in stored
    assert container.credentials.resolve(reference) is None


def test_plugin_secret_reference_has_fixed_credential_safe_length() -> None:
    reference = _secret_reference("p" * 512, "k" * 128)

    assert reference.startswith("plugin.")
    assert len(reference) <= 128


def test_tampered_secret_reference_cannot_cross_credential_namespace() -> None:
    container = build_container()
    container.credentials.put("openai", "provider-private-secret")
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "text-tools": {
                    "schema_version": 1,
                    "values": {},
                    "secret_refs": {"api_key": "openai"},
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ExtensionError) as read_error:
        container.plugins.get_settings("text-tools")
    with pytest.raises(ExtensionError) as uninstall_error:
        container.plugins.uninstall("text-tools")

    assert read_error.value.code == "PLUGIN_STORAGE_ERROR"
    assert uninstall_error.value.code == "PLUGIN_STORAGE_ERROR"
    assert container.credentials.resolve("openai") == "provider-private-secret"


def test_secret_delete_restores_reference_when_credential_delete_fails(
    monkeypatch,
) -> None:
    container = build_container()
    container.plugins.put_setting_secret("text-tools", "api_key", "keep-me")
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    original = settings_path.read_text(encoding="utf-8")
    reference = _secret_reference("text-tools", "api_key")

    def fail_delete(_credential_id: str) -> bool:
        raise CredentialStoreError("injected delete failure")

    monkeypatch.setattr(container.credentials, "delete", fail_delete)

    with pytest.raises(ExtensionError) as exc:
        container.plugins.delete_setting_secret("text-tools", "api_key")

    assert exc.value.code == "PLUGIN_SECRET_STORE_ERROR"
    assert settings_path.read_text(encoding="utf-8") == original
    assert container.credentials.resolve(reference) == "keep-me"


def test_uninstall_restores_settings_when_atomic_secret_delete_fails(
    monkeypatch,
) -> None:
    container = build_container()
    container.plugins.update_settings("text-tools", 1, {"result_limit": 12})
    container.plugins.put_setting_secret("text-tools", "api_key", "keep-me")
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    original = settings_path.read_text(encoding="utf-8")
    reference = _secret_reference("text-tools", "api_key")

    def fail_delete_many(_credential_ids: list[str]) -> set[str]:
        raise CredentialStoreError("injected batch delete failure")

    monkeypatch.setattr(container.credentials, "delete_many", fail_delete_many)

    with pytest.raises(ExtensionError) as exc:
        container.plugins.uninstall("text-tools")

    assert exc.value.code == "PLUGIN_SECRET_STORE_ERROR"
    assert settings_path.read_text(encoding="utf-8") == original
    assert container.credentials.resolve(reference) == "keep-me"
    assert container.plugins.get("text-tools").manifest.plugin_id == "text-tools"


def test_invalid_command_and_settings_manifest_are_rejected(tmp_path: Path) -> None:
    invalid_command = tmp_path / "invalid-command"
    invalid_command.mkdir()
    (invalid_command / "plugin.yaml").write_text(
        """
id: invalid-command
name: Invalid Command
version: 1.0.0
contributes:
  commands: [other.run]
""".strip(),
        encoding="utf-8",
    )
    (invalid_command / "commands.yaml").write_text(
        """
commands:
  - command_id: other.run
    title: Invalid
    locations: [command_palette]
    handler: echo
""".strip(),
        encoding="utf-8",
    )

    invalid_settings = tmp_path / "invalid-settings"
    invalid_settings.mkdir()
    (invalid_settings / "plugin.yaml").write_text(
        """
id: invalid-settings
name: Invalid Settings
version: 1.0.0
contributes:
  settings_sections: [invalid-settings.general]
""".strip(),
        encoding="utf-8",
    )
    (invalid_settings / "settings.yaml").write_text(
        """
section_id: invalid-settings.general
schema_version: 1
fields:
  - key: token
    label: Token
    type: secret
    default: leaked-default
""".strip(),
        encoding="utf-8",
    )

    runtime = PluginRuntime(ToolRegistry())
    with pytest.raises(ExtensionError) as command_error:
        runtime.install(invalid_command)
    assert command_error.value.code == "PLUGIN_COMMAND_INVALID"

    with pytest.raises(ExtensionError) as settings_error:
        runtime.install(invalid_settings)
    assert settings_error.value.code == "PLUGIN_SETTINGS_SCHEMA_INVALID"


@pytest.mark.parametrize("bound", [".nan", ".inf", "-.inf"])
def test_non_finite_setting_bounds_are_rejected(tmp_path: Path, bound: str) -> None:
    package = tmp_path / f"invalid-bound-{bound.replace('.', 'dot').replace('-', 'neg')}"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: invalid-bound
name: Invalid Bound
version: 1.0.0
contributes:
  settings_sections: [invalid-bound.general]
backend:
  type: none
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "settings.yaml").write_text(
        f"""
section_id: invalid-bound.general
schema_version: 1
fields:
  - key: limit
    label: Limit
    type: number
    minimum: {bound}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ExtensionError) as exc:
        PluginRuntime(ToolRegistry()).install(package)

    assert exc.value.code == "PLUGIN_SETTINGS_SCHEMA_INVALID"
    assert "must be finite" in exc.value.message


def test_null_command_list_returns_stable_manifest_error(tmp_path: Path) -> None:
    package = tmp_path / "null-commands"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: null-commands
name: Null Commands
version: 1.0.0
contributes:
  commands: []
backend:
  type: internal_rpc
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "commands.yaml").write_text("commands:\n", encoding="utf-8")

    with pytest.raises(ExtensionError) as exc:
        PluginRuntime(ToolRegistry()).install(package)

    assert exc.value.code == "EXTENSION_MANIFEST_INVALID"


def test_external_command_schema_reference_is_rejected(tmp_path: Path) -> None:
    package = tmp_path / "external-ref"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: external-ref
name: External Ref
version: 1.0.0
contributes:
  commands: [external-ref.run]
backend:
  type: internal_rpc
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "commands.yaml").write_text(
        """
commands:
  - command_id: external-ref.run
    title: External Ref
    locations: [command_palette]
    handler: echo
    parameters:
      $ref: file:///host/private-schema.json
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ExtensionError) as exc:
        PluginRuntime(ToolRegistry()).install(package)

    assert exc.value.code == "PLUGIN_COMMAND_INVALID"
    assert "External JSON Schema reference" in exc.value.message


def test_settings_missing_and_secret_field_errors_are_stable() -> None:
    container = build_container()

    with pytest.raises(ExtensionError) as missing:
        container.plugins.get_settings("does-not-exist")
    assert missing.value.code == "PLUGIN_NOT_FOUND"

    with pytest.raises(ExtensionError) as field:
        container.plugins.put_setting_secret("text-tools", "result_limit", "secret")
    assert field.value.code == "PLUGIN_SECRET_FIELD_NOT_FOUND"

    with pytest.raises(ExtensionError) as empty:
        container.plugins.put_setting_secret("text-tools", "api_key", "")
    assert empty.value.code == "PLUGIN_SECRET_VALUE_INVALID"


def test_corrupted_plugin_settings_namespace_returns_stable_error() -> None:
    container = build_container()
    settings_path = get_settings().data_dir / "plugins" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text('{"text-tools": []}', encoding="utf-8")

    with pytest.raises(ExtensionError) as exc:
        container.plugins.get_settings("text-tools")

    assert exc.value.code == "PLUGIN_STORAGE_ERROR"

    with pytest.raises(ExtensionError) as secret_exc:
        container.plugins.put_setting_secret("text-tools", "api_key", "must-not-orphan")

    assert secret_exc.value.code == "PLUGIN_STORAGE_ERROR"
    credentials_path = get_settings().data_dir / "credentials" / "credentials.json"
    credential_ids = (
        json.loads(credentials_path.read_text(encoding="utf-8")).keys()
        if credentials_path.exists()
        else []
    )
    assert not any(item.startswith("plugin.") for item in credential_ids)
