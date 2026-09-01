import asyncio
import shutil
import time

import pytest

from app.agent.permissions import PermissionMode
from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import (
    AgentRunCreateRequest,
    AgentRunStatus,
    SkillStatus,
    ToolCall,
)
from app.extensions import ExtensionError
from app.services import note_service
from app.config import BACKEND_DIR, get_settings


MCP_FIXTURE = BACKEND_DIR / "extensions" / "fixtures" / "mcp-echo"


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def mcp_container():
    container = build_container()
    installed = container.plugins.install(MCP_FIXTURE)
    assert installed.status == "permission_required"
    container.plugins.set_permissions("mcp-fixture", ["notes.read"])
    try:
        yield container
    finally:
        container.plugins.shutdown()


def test_bundled_plugin_registers_tool_and_skill_is_ready() -> None:
    async def scenario() -> None:
        container = build_container()

        plugin = container.plugins.get("text-tools")
        skill = container.skills.get("knowledge-assistant")
        definition = container.tools.get("text.uppercase").definition
        result = await container.tools.execute(
            ToolCall(
                tool_call_id="call_uppercase",
                name="text.uppercase",
                arguments={"text": "hello plugin"},
            ),
            ToolExecutionContext(run_id="run_extension_test"),
        )

        assert plugin.enabled is True and plugin.status == "ready"
        assert skill.enabled is True and skill.status == SkillStatus.ready
        assert definition.source == "plugin"
        assert result.success is True
        assert result.output == {"text": "HELLO PLUGIN"}

    run(scenario())


def test_skill_drives_agent_and_can_call_plugin_tool() -> None:
    async def scenario() -> None:
        container = build_container()
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool text.uppercase {"text":"skill plugin"}',
                provider_id="mock",
                model="mock-1",
                skill_id="knowledge-assistant",
            )
        )

        completed = await container.agent.wait(created.run_id)

        assert completed.status == AgentRunStatus.completed
        assert completed.tool_results[0].success is True
        assert completed.tool_results[0].output == {"text": "SKILL PLUGIN"}

    run(scenario())


def test_plugin_disable_updates_skill_dependency_status() -> None:
    container = build_container()

    disabled = container.plugins.disable("text-tools")
    skill = container.skills.get("knowledge-assistant")

    assert disabled.status == "disabled"
    assert not container.tools.contains("text.uppercase")
    assert skill.status == SkillStatus.dependency_missing
    assert skill.missing_dependencies == ["text.uppercase"]

    with pytest.raises(ExtensionError) as exc:
        container.skills.build_agent_configuration(
            "knowledge-assistant",
            container.providers.get("mock").config.capabilities,
        )
    assert exc.value.code == "SKILL_NOT_READY"

    container.plugins.enable("text-tools")
    assert container.skills.get("knowledge-assistant").status == SkillStatus.ready


def test_enabled_skill_blocks_plugin_uninstall() -> None:
    container = build_container()
    plugin = container.plugins.get("text-tools")
    dependencies = container.skills.depending_on_tools(plugin.manifest.contributes.tools)

    with pytest.raises(ExtensionError) as exc:
        container.plugins.uninstall("text-tools", dependencies)

    assert exc.value.code == "PLUGIN_IN_USE"
    assert exc.value.details["skills"] == ["knowledge-assistant"]


def test_agent_note_search_tool_collects_citations() -> None:
    async def scenario() -> None:
        container = build_container()
        await note_service.create_note(
            title="Agent 检索",
            markdown="# Agent\n\nAgent 可以通过工具检索本地知识库。",
            folder="",
            tags=["agent"],
        )
        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool notes.search {"query":"本地知识库","mode":"fts"}',
                provider_id="mock",
                model="mock-1",
                skill_id="knowledge-assistant",
            )
        )

        completed = await container.agent.wait(created.run_id)

        assert completed.status == AgentRunStatus.completed
        assert completed.tool_results[0].success is True
        assert completed.citations
        assert completed.citations[0].file_path == "Agent 检索.md"

    run(scenario())


def test_network_tool_requires_run_level_network_permission() -> None:
    async def scenario() -> None:
        container = build_container()
        tool = container.tools.get("system.echo")
        tool.definition.permission = "network.request"
        container.permissions.policy.set_rule("network.request", PermissionMode.allow)

        created = await container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool system.echo {"text":"network"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["system.echo"],
                allow_network=False,
            )
        )
        completed = await container.agent.wait(created.run_id)

        assert completed.tool_results[0].success is False
        assert completed.tool_results[0].error_code == "NETWORK_NOT_ALLOWED"

    run(scenario())


def test_skill_install_reports_missing_tool_dependency(tmp_path) -> None:
    package = tmp_path / "missing-tool-skill"
    package.mkdir()
    (package / "skill.yaml").write_text(
        """
id: missing-tool
name: Missing Tool
version: 1.0.0
tools: [plugin.not-installed]
""".strip(),
        encoding="utf-8",
    )
    container = build_container()

    installed = container.skills.install(package)

    assert installed.status == SkillStatus.dependency_missing
    assert installed.missing_dependencies == ["plugin.not-installed"]
    with pytest.raises(ExtensionError) as exc:
        container.skills.enable("missing-tool")
    assert exc.value.code == "SKILL_DEPENDENCY_MISSING"


def test_plugin_permissions_must_be_known_and_granted(tmp_path) -> None:
    package = tmp_path / "write-plugin"
    package.mkdir()
    (package / "plugin.yaml").write_text(
        """
id: write-plugin
name: Write Plugin
version: 1.0.0
permissions: [notes.write]
contributes:
  tools: [plugin.write]
backend:
  type: internal_rpc
  transport: none
""".strip(),
        encoding="utf-8",
    )
    (package / "tools.yaml").write_text(
        """
tools:
  - name: plugin.write
    description: permission test
    permission: notes.write
    handler: echo
    parameters:
      type: object
      properties: {text: {type: string}}
      required: [text]
""".strip(),
        encoding="utf-8",
    )
    container = build_container()

    installed = container.plugins.install(package)
    assert installed.status == "permission_required"
    with pytest.raises(ExtensionError) as exc:
        container.plugins.enable("write-plugin")
    assert exc.value.code == "PLUGIN_PERMISSION_REQUIRED"

    granted = container.plugins.set_permissions("write-plugin", ["notes.write"])
    enabled = container.plugins.enable("write-plugin")
    assert granted.granted_permissions == ["notes.write"]
    assert enabled.status == "ready"


def test_plugin_rejects_unknown_permissions_and_invalid_schema(tmp_path) -> None:
    unknown = tmp_path / "unknown-permission"
    unknown.mkdir()
    (unknown / "plugin.yaml").write_text(
        """
id: unknown-permission
name: Unknown
version: 1.0.0
permissions: [notes.wirte]
""".strip(),
        encoding="utf-8",
    )
    container = build_container()
    with pytest.raises(ExtensionError) as exc:
        container.plugins.install(unknown)
    assert exc.value.code == "EXTENSION_PERMISSION_INVALID"

    malformed = tmp_path / "malformed-schema"
    malformed.mkdir()
    (malformed / "plugin.yaml").write_text(
        """
id: malformed-schema
name: Malformed
version: 1.0.0
contributes:
  tools: [bad.schema]
""".strip(),
        encoding="utf-8",
    )
    (malformed / "tools.yaml").write_text(
        """
tools:
  - name: bad.schema
    description: invalid schema
    handler: echo
    parameters:
      type: object
      properties: []
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ExtensionError) as exc:
        container.plugins.install(malformed)
    assert exc.value.code == "PLUGIN_TOOL_SCHEMA_INVALID"


def test_attachment_and_transcription_tools_use_host_storage() -> None:
    async def scenario() -> None:
        root = get_settings().attachments_path
        root.mkdir(parents=True, exist_ok=True)
        (root / "meeting.txt").write_text("会议转写内容", encoding="utf-8")
        container = build_container()

        attachment = await container.tools.execute(
            ToolCall(
                tool_call_id="call_attachment",
                name="attachments.read",
                arguments={"attachment_id": "meeting.txt"},
            ),
            ToolExecutionContext(run_id="run_attachment"),
        )
        transcription = await container.tools.execute(
            ToolCall(
                tool_call_id="call_transcription",
                name="audio.transcribe",
                arguments={"attachment_id": "meeting.txt"},
            ),
            ToolExecutionContext(run_id="run_transcription"),
        )

        assert attachment.success is True
        assert attachment.output["content"] == "会议转写内容"
        assert transcription.success is True
        assert transcription.output["status"] == "completed"
        assert transcription.output["text"] == "会议转写内容"

    run(scenario())


def test_mcp_stdio_host_discovers_namespaced_tools_and_maps_results(
    mcp_container, monkeypatch
) -> None:
    async def scenario() -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "must-not-enter-plugin-host")
        enabled = mcp_container.plugins.enable("mcp-fixture")
        status = mcp_container.plugins.get_host_status("mcp-fixture")
        definition = mcp_container.tools.get("mcp-fixture.echo").definition
        result = await mcp_container.tools.execute(
            ToolCall(
                tool_call_id="call_mcp_echo",
                name="mcp-fixture.echo",
                arguments={"text": "hello mcp"},
            ),
            ToolExecutionContext(
                run_id="run_mcp_fixture", tool_call_id="call_mcp_echo"
            ),
        )

        assert enabled.status == "ready" and enabled.enabled is True
        assert status.status == "ready"
        environment = await mcp_container.tools.execute(
            ToolCall(
                tool_call_id="call_mcp_environment",
                name="mcp-fixture.environment",
                arguments={},
            ),
            ToolExecutionContext(run_id="run_mcp_fixture"),
        )

        assert status.tools_count == 6
        assert status.protocol_version == "2025-11-25"
        assert status.server_name == "notesagent-mcp-fixture"
        assert definition.permission == "notes.read"
        assert result.success is True
        assert result.output == {"echo": "hello mcp"}
        assert environment.success is True
        assert environment.output == {
            "has_openai_key": False,
            "has_app_db_path": False,
        }

        disabled = mcp_container.plugins.disable("mcp-fixture")
        assert disabled.status == "disabled"
        assert mcp_container.plugins.get_host_status("mcp-fixture").status == "stopped"
        assert not mcp_container.tools.contains("mcp-fixture.echo")

    run(scenario())


def test_agent_calls_mcp_tool_through_registry_and_writes_trace(mcp_container) -> None:
    async def scenario() -> None:
        mcp_container.plugins.enable("mcp-fixture")
        created = await mcp_container.agent.create_run(
            AgentRunCreateRequest(
                input='/tool mcp-fixture.echo {"text":"agent mcp"}',
                provider_id="mock",
                model="mock-1",
                allowed_tools=["mcp-fixture.echo"],
            )
        )
        completed = await mcp_container.agent.wait(created.run_id)
        trace = mcp_container.agent.get_trace(
            created.run_id, after_sequence=-1, limit=100
        )

        assert completed.status == AgentRunStatus.completed
        assert completed.tool_results[0].success is True
        assert completed.tool_results[0].output == {"echo": "agent mcp"}
        assert any(
            item.event == "ToolCall" and item.data.get("name") == "mcp-fixture.echo"
            for item in trace.items
        )

    run(scenario())


def test_mcp_business_error_size_limit_and_timeout_are_structured(mcp_container) -> None:
    async def scenario() -> None:
        mcp_container.plugins.enable("mcp-fixture")
        context = ToolExecutionContext(run_id="run_mcp_errors")

        failed = await mcp_container.tools.execute(
            ToolCall(tool_call_id="call_fail", name="mcp-fixture.fail", arguments={}),
            context,
        )
        oversized = await mcp_container.tools.execute(
            ToolCall(tool_call_id="call_large", name="mcp-fixture.large", arguments={}),
            context,
        )
        timed_out = await mcp_container.tools.execute(
            ToolCall(
                tool_call_id="call_sleep",
                name="mcp-fixture.sleep",
                arguments={"seconds": 5},
            ),
            ToolExecutionContext(
                run_id="run_mcp_errors", tool_call_id="call_sleep"
            ),
        )
        recovered = await mcp_container.tools.execute(
            ToolCall(
                tool_call_id="call_after_timeout",
                name="mcp-fixture.echo",
                arguments={"text": "still ready"},
            ),
            context,
        )

        assert failed.success is False
        assert failed.error_code == "MCP_TOOL_CALL_FAILED"
        assert failed.error_message == "fixture failure"
        assert oversized.success is False
        assert oversized.error_code == "MCP_TOOL_RESULT_TOO_LARGE"
        assert timed_out.success is False
        assert timed_out.error_code == "MCP_TOOL_CALL_FAILED"
        assert recovered.success is True
        assert mcp_container.plugins.get_host_status("mcp-fixture").status == "ready"

    run(scenario())


def test_mcp_abnormal_exit_unregisters_tools_and_restart_recovers(mcp_container) -> None:
    async def scenario() -> None:
        mcp_container.plugins.enable("mcp-fixture")
        crashed = await mcp_container.tools.execute(
            ToolCall(tool_call_id="call_exit", name="mcp-fixture.exit", arguments={}),
            ToolExecutionContext(run_id="run_mcp_exit", tool_call_id="call_exit"),
        )

        deadline = time.monotonic() + 2
        while mcp_container.tools.contains("mcp-fixture.echo") and time.monotonic() < deadline:
            await asyncio.sleep(0.02)

        plugin = mcp_container.plugins.get("mcp-fixture")
        status = mcp_container.plugins.get_host_status("mcp-fixture")
        assert crashed.success is False
        assert crashed.error_code == "PLUGIN_HOST_UNAVAILABLE"
        assert plugin.status == "error" and plugin.enabled is False
        assert status.status == "unhealthy"
        assert not mcp_container.tools.contains("mcp-fixture.echo")

        restarted = mcp_container.plugins.restart_host("mcp-fixture")
        assert restarted.status == "ready"
        assert restarted.tools_count == 6
        assert mcp_container.tools.contains("mcp-fixture.echo")

    run(scenario())


@pytest.mark.parametrize(
    ("mode", "contributions", "expected_code"),
    [
        ("no-tools", "[]", "MCP_CAPABILITY_UNSUPPORTED"),
        ("invalid-schema", "[mcp-invalid.broken]", "MCP_TOOL_SCHEMA_INVALID"),
    ],
)
def test_mcp_rejects_missing_capability_and_invalid_discovery(
    tmp_path, mode, contributions, expected_code
) -> None:
    package = tmp_path / f"mcp-{mode}"
    package.mkdir()
    shutil.copyfile(MCP_FIXTURE / "server.py", package / "server.py")
    (package / "plugin.yaml").write_text(
        f"""
id: mcp-invalid
name: Invalid MCP Fixture
version: 1.0.0
contributes:
  tools: {contributions}
backend:
  type: mcp
  transport: stdio
  command: python
  args: [server.py, {mode}]
  startup_timeout_seconds: 5
  tool_timeout_seconds: 1
""".strip(),
        encoding="utf-8",
    )
    container = build_container()
    container.plugins.install(package)
    try:
        with pytest.raises(ExtensionError) as exc:
            container.plugins.enable("mcp-invalid")
        assert exc.value.code == expected_code
        assert container.plugins.get("mcp-invalid").status == "error"
        assert container.plugins.get_host_status("mcp-invalid").status == "error"
        assert not container.tools.contains("mcp-invalid.broken")
    finally:
        container.plugins.shutdown()
