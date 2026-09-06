from dataclasses import dataclass

from app.agent import AgentRuntime, PermissionManager, PermissionPolicy, ToolRegistry
from app.agent.builtin_tools import register_builtin_tools
from app.contracts import ModelCapability, ProviderConfig, ProviderType
from app.config import BACKEND_DIR, get_settings
from app.extensions import PluginRuntime, SkillRuntime
from app.extensions.installed import InstalledRuntime
from app.extensions.mcp_registry import McpServerRegistry
from app.providers import MockProvider, ProviderFactory, ProviderRegistry
from app.providers.routing import ModelRoutingService
from app.providers.credentials import (
    ChainedCredentialResolver,
    EncryptedCredentialStore,
    EnvironmentCredentialResolver,
)


@dataclass(frozen=True)
class ApplicationContainer:
    providers: ProviderRegistry
    provider_factory: ProviderFactory
    model_routing: ModelRoutingService
    credentials: EncryptedCredentialStore
    tools: ToolRegistry
    permissions: PermissionManager
    skills: SkillRuntime
    plugins: PluginRuntime
    mcp_servers: McpServerRegistry
    agent: AgentRuntime


def build_container() -> ApplicationContainer:
    settings = get_settings()
    credentials = EncryptedCredentialStore()
    provider_factory = ProviderFactory(
        ChainedCredentialResolver(credentials, EnvironmentCredentialResolver())
    )
    providers = ProviderRegistry(provider_factory)
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
        credentials=credentials,
        # 当前 Python Host 尚无 OS 沙箱。生产构建必须保持关闭，直到
        # Tauri/Rust Host 能签发绑定命令摘要的可信启动许可。
        allow_unsandboxed_mcp=settings.environment == "development",
    )
    plugins.install(BACKEND_DIR / "extensions" / "plugins" / "text-tools")
    plugins.enable("text-tools")
    plugins.install(BACKEND_DIR / "extensions" / "plugins" / "chat-policy")
    plugins.enable("chat-policy")
    plugins = InstalledRuntime(plugins, 'plugin', settings.data_dir)
    plugins.restore()

    mcp_servers = McpServerRegistry(
        tools,
        credentials,
        settings.data_dir,
        allow_process_launch=settings.environment == "development",
    )
    mcp_servers.restore_enabled()

    skills = SkillRuntime(tools)
    skills.install(BACKEND_DIR / "extensions" / "skills" / "knowledge-assistant")
    if not skills.get("knowledge-assistant").missing_dependencies:
        skills.enable("knowledge-assistant")
    skills.install(BACKEND_DIR / "extensions" / "skills" / "chat-operator")
    if not skills.get("chat-operator").missing_dependencies:
        skills.enable("chat-operator")
    skills = InstalledRuntime(skills, 'skill', settings.data_dir)
    skills.restore()

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
        model_routing=_local_model_routing(providers, provider_factory.credentials),
        credentials=credentials,
        tools=tools,
        permissions=permissions,
        skills=skills,
        plugins=plugins,
        mcp_servers=mcp_servers,
        agent=agent,
    )


def _local_model_routing(providers, credentials):
    from app.local_models.runtime import LocalEmbedding, LocalSpeech
    return ModelRoutingService(providers, credentials, local_embedding=LocalEmbedding(), local_speech=LocalSpeech())


container = build_container()
