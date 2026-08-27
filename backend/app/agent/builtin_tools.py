from pydantic import BaseModel, ConfigDict

from app.agent.tools import ToolExecutionContext, ToolRegistry
from app.contracts import ToolDefinition


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EchoArguments(ToolArguments):
    text: str


class AddArguments(ToolArguments):
    left: float
    right: float


async def echo(arguments: EchoArguments, _: ToolExecutionContext) -> dict[str, str]:
    return {"text": arguments.text}


async def add(arguments: AddArguments, _: ToolExecutionContext) -> dict[str, float]:
    return {"value": arguments.left + arguments.right}


def register_builtin_tools(registry: ToolRegistry) -> None:
    registry.register(
        ToolDefinition(
            name="system.echo",
            description="Echo text for local Agent integration testing.",
            parameters=EchoArguments.model_json_schema(),
        ),
        EchoArguments,
        echo,
    )
    registry.register(
        ToolDefinition(
            name="math.add",
            description="Add two numbers without external side effects.",
            parameters=AddArguments.model_json_schema(),
        ),
        AddArguments,
        add,
    )
