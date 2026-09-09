import asyncio

from app.agent.tools import ToolExecutionContext
from app.container import build_container
from app.contracts import ToolCall


def execute(container, name: str, arguments: dict, call_id: str = "call_service_tool"):
    return asyncio.run(container.tools.execute(
        ToolCall(tool_call_id=call_id, name=name, arguments=arguments),
        ToolExecutionContext(run_id="run_service_tools", tool_call_id=call_id),
    ))


def test_service_tool_catalog_is_registered_with_permissions() -> None:
    container = build_container()
    expected = {
        "function_plot.compose": None,
        "notes.rename": "notes.write",
        "notes.delete": "notes.delete",
        "tasks.read": "tasks.read",
        "tasks.delete": "tasks.write",
        "audio.transcription_status": "attachments.read",
        "skills.list": None,
        "skills.create": "skills.write",
        "skills.update": "skills.write",
        "plugins.list": None,
        "plugins.create": "plugins.write",
    }
    assert {name: container.tools.get(name).definition.permission for name in expected} == expected
    assert container.skills.get("chat-operator").status.value == "ready"


def test_function_plot_tool_builds_a_valid_markdown_block() -> None:
    container = build_container()
    result = execute(container, "function_plot.compose", {
        "expressions": ["sin(x)", "x^2 / 5"],
        "domain": [-6, 6],
        "y_range": [-2, 8],
        "xlabel": "x",
        "ylabel": "y",
    })
    assert result.success is True
    assert result.output["markdown"].startswith("```function-plot\n")
    assert "domain: -6, 6" in result.output["source"]
    assert result.output["expression_count"] == 2
    assert result.output["node_count"] > 0
    assert result.output["persisted"] is False


def test_function_plot_tool_rejects_unsafe_expressions() -> None:
    container = build_container()
    result = execute(container, "function_plot.compose", {"expressions": ["__import__('os')"]})
    assert result.success is False
    assert result.error_code == "FUNCTION_PLOT_INVALID"


def test_plugin_create_installs_a_disabled_safe_declarative_plugin() -> None:
    container = build_container()
    result = execute(container, "plugins.create", {
        "plugin_id": "agent-sample",
        "name": "Agent Sample",
        "description": "A bounded declarative test plugin.",
        "tools": [{
            "name": "agent-sample.uppercase",
            "description": "Uppercase the supplied text.",
            "handler": "uppercase",
        }],
    }, "call_create_plugin_a1")
    assert result.success is True
    assert result.output["created"] is True
    assert result.output["enabled"] is False
    assert result.output["requires_enable"] is True
    assert result.output["safety_profile"] == "declarative-host-handlers-only"
    assert container.tools.contains("agent-sample.uppercase") is False

    installed = container.plugins.get("agent-sample")
    assert installed.status.value == "installed"
    assert installed.enabled is False
