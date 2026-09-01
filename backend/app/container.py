from dataclasses import dataclass

from app.agent import AgentRuntime, PermissionManager, PermissionPolicy, ToolRegistry
from app.agent.builtin_tools import register_builtin_tools
from app.contracts import ModelCapability, ProviderConfig, ProviderType
from app.config import BACKEND_DIR, get_settings
from app.extensions import PluginRuntime, SkillRuntime
from app.providers import MockProvider, ProviderFactory, ProviderRegistry
from app.providers.credentials import (
    ChainedCredentialResolver,
    EncryptedCredentialStore,
    EnvironmentCredentialResolver,
)


@dataclass(frozen=True)
class ApplicationContainer:
    providers: ProviderRegistry
    provider_factory: ProviderFactory
    credentials: EncryptedCredentialStore
    tools: ToolRegistry
    permissions: PermissionManager
    skills: SkillRuntime
    plugins: PluginRuntime
    agent: AgentRuntime


def build_container() -> ApplicationContainer:
    settings = get_settings()
    credentials = EncryptedCredentialStore()
    provider_factory = ProviderFactory(
        ChainedCredentialResolver(credentials, EnvironmentCredentialResolver())
    )
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

    plugins = PluginRuntime(
        tools,
        # 当前 Python Host 尚无 OS 沙箱。生产构建必须保持关闭，直到
        # Tauri/Rust Host 能签发绑定命令摘要的可信启动许可。
        allow_unsandboxed_mcp=settings.environment == "development",
    )
    plugins.install(BACKEND_DIR / "extensions" / "plugins" / "text-tools")
    plugins.enable("text-tools")

    skills = SkillRuntime(tools)
    skills.install(BACKEND_DIR / "extensions" / "skills" / "knowledge-assistant")
    skills.enable("knowledge-assistant")

    policy = PermissionPolicy()
    permissions = PermissionManager(policy)
    agent = AgentRuntime(
        providers=providers,
        tools=tools,
        permissions=permissions,
        skills=skills,
    )
    return ApplicationContainer(
        providers=providers,
        provider_factory=provider_factory,
        credentials=credentials,
        tools=tools,
        permissions=permissions,
        skills=skills,
        plugins=plugins,
        agent=agent,
    )


container = build_container()
