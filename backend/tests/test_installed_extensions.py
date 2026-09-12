from pathlib import Path
import pytest
from app.agent.tools import ToolRegistry
from app.extensions import SkillRuntime
from app.extensions.installed import InstalledRuntime


def package(root):
    root.mkdir(parents=True)
    (root / 'skill.yaml').write_text('skill_id: audit\nname: Audit\nversion: 1.0.0\npermissions: []\ntools: []\n', encoding='utf-8')
    return root


def runtime(data):
    return InstalledRuntime(SkillRuntime(ToolRegistry()), 'skill', data)


def test_restores_enabled_and_disabled_without_deleting_directory_install(tmp_path):
    root = package(tmp_path / 'user-source')
    data = tmp_path / 'data'
    first = runtime(data); first.install(root); first.enable('audit')
    second = runtime(data); second.restore()
    assert second.get('audit').enabled
    second.disable('audit')
    third = runtime(data); third.restore()
    assert not third.get('audit').enabled
    third.uninstall('audit')
    assert root.exists()
    fourth = runtime(data); fourth.restore()
    assert fourth.list() == []


def test_owned_zip_removed_and_changed_packages_not_auto_enabled(tmp_path):
    data = tmp_path / 'data'
    owned = data / 'extension-packages/skill-test'
    root = package(owned / 'nested')
    first = runtime(data); first.install(root, managed_root=owned); first.enable('audit')
    (root / 'prompt.md').write_text('changed', encoding='utf-8')
    first.disable('audit')
    with pytest.raises(Exception, match='Package changed'):
        first.enable('audit')
    second = runtime(data); second.restore()
    assert second.list() == []
    assert second.restore_errors[0]['id'] == 'audit'
    first.uninstall('audit')
    assert not owned.exists()


def test_rejects_claiming_user_directory_as_managed(tmp_path):
    root = package(tmp_path / 'source')
    with pytest.raises(ValueError, match='managed'):
        runtime(tmp_path / 'data').install(root, managed_root=root)
    assert root.exists()


def test_builtin_disabled_plugin_does_not_break_startup():
    from app.container import build_container
    first = build_container()
    first.plugins.disable('text-tools')
    second = build_container()
    assert not second.plugins.get('text-tools').enabled
    assert second.skills.get('knowledge-assistant').missing_dependencies
    second.plugins.enable('text-tools')
    third = build_container()
    assert third.plugins.get('text-tools').enabled
    assert third.skills.get('knowledge-assistant').enabled
    for container in (first, second, third):
        container.plugins.shutdown(); container.mcp_servers.shutdown()


def test_rust_ownership_marker_rejects_every_legacy_python_write(tmp_path):
    root = package(tmp_path / 'source')
    data = tmp_path / 'data'
    instance = runtime(data)
    instance.install(root)
    (data / 'extension-installations.rust-owned.json').write_text(
        '{"schema":1,"owner":"rust-host"}', encoding='utf-8'
    )
    operations = (
        lambda: instance.install(root),
        lambda: instance.enable('audit'),
        lambda: instance.disable('audit'),
        lambda: instance.set_permissions('audit', []),
        lambda: instance.uninstall('audit'),
    )
    for operation in operations:
        with pytest.raises(Exception) as error:
            operation()
        assert getattr(error.value, 'code', None) == 'EXTENSION_HOST_OWNED'
    restored = runtime(data)
    restored.restore()
    assert all(item.manifest.skill_id != 'audit' for item in restored.list())
