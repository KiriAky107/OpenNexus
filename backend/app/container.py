from dataclasses import dataclass

from app.agent import AgentRuntime, PermissionManager, PermissionPolicy, ToolRegistry
from app.agent.builtin_tools import register_builtin_tools
from app.contracts import ModelCapability, ProviderConfig, ProviderType
from app.providers import MockProvider, ProviderFactory, ProviderRegistry
from app.providers.credentials import EnvironmentCredentialResolver


@dataclass(frozen=True)
class ApplicationContainer:
    providers: ProviderRegistry
    provider_factory: ProviderFactory
    tools: ToolRegistry
    permissions: PermissionManager
    agent: AgentRuntime


def build_container() -> ApplicationContainer:
    provider_factory = ProviderFactory(EnvironmentCredentialResolver())
    providers = ProviderRegistry()
    providers.register(
        ProviderConfig(
            provider_id="mock",
            provider_type=ProviderType.mock,
            name="Mock Provider",
            default_model="mock-1",
            enabled=True,
            capabilities=[
                ModelCapability.chat,
                ModelCapability.tool_calling,
                ModelCapability.streaming,
            ],
        ),
        MockProvider(),
    )

    tools = ToolRegistry()
    register_builtin_tools(tools)

    policy = PermissionPolicy()
    permissions = PermissionManager(policy)
    agent = AgentRuntime(providers=providers, tools=tools, permissions=permissions)
    return ApplicationContainer(
        providers=providers,
        provider_factory=provider_factory,
        tools=tools,
        permissions=permissions,
        agent=agent,
    )


container = build_container()
