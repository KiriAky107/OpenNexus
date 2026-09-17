import asyncio
import importlib.util
from pathlib import Path

import pytest

from app.config import BACKEND_DIR
from app.container import build_container
from app.contracts import ModelCapability, PluginCommandContext, ToolCall
from app.agent.tools import ToolExecutionContext
from app.extensions.archive import install_zip

ROOT = BACKEND_DIR / 'extensions/community'


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_analysis_ignores_metadata_and_code_and_keeps_line_numbers():
    server = load(ROOT / 'plugins/markdown-workbench/server.py')
    sample = (ROOT / 'plugins/markdown-workbench/example.md').read_text(encoding='utf-8')
    report = server.inspect_markdown(sample)
    assert report['summary']['headings'] == 3
    assert report['summary']['tasks'] == 2
    assert report['summary']['open_tasks'] == 1
    assert [(item['line'], item['code']) for item in report['issues']] == [(7, 'heading_jump'), (11, 'duplicate_heading')]
    assert report['tasks'][0]['line'] == 8
    assert server.inspect_markdown('Title\n===\n\nSubtitle\n---')['summary']['headings'] == 2
    assert server.inspect_markdown('```\n# code')['issues'][0]['code'] == 'unclosed_fence'
    with pytest.raises(ValueError):
        server.inspect_markdown('x' * 100001)
    many = server.inspect_markdown('\n'.join('- [ ] task' for _ in range(205)))
    assert many['truncated'] and many['summary']['tasks'] == 205 and len(many['tasks']) == 200


def test_zip_install_real_mcp_tool_command_and_skill(tmp_path):
    builder = load(ROOT / 'build_packages.py')
    output = tmp_path / 'dist'
    catalog = builder.build(output)
    assert builder.build(output) == catalog
    runtime = build_container()
    sample = (ROOT / 'plugins/markdown-workbench/example.md').read_text(encoding='utf-8')
    async def run():
        plugin = install_zip((output / 'markdown-workbench-1.0.0.zip').read_bytes(), 'plugin', tmp_path / 'installed', runtime.plugins.install)
        assert not plugin.enabled
        skill = install_zip((output / 'note-reviewer-1.0.0.zip').read_bytes(), 'skill', tmp_path / 'installed', runtime.skills.install)
        assert 'markdown-workbench.inspect_markdown' in skill.missing_dependencies
        assert runtime.plugins.enable('markdown-workbench').status == 'ready'
        result = await runtime.tools.execute(ToolCall(tool_call_id='community-test', name='markdown-workbench.inspect_markdown', arguments={'text': sample}), ToolExecutionContext(run_id='community-test'))
        assert result.success, result.error_message
        assert result.output['summary']['issues'] == 2
        command = await runtime.plugins.execute_command('markdown-workbench.inspect-selection', {}, PluginCommandContext(selection=sample))
        assert '1 项未完成任务' in command.effect.payload.message
        assert runtime.skills.enable('note-reviewer').status == 'ready'
        config = runtime.skills.build_agent_configuration('note-reviewer', [ModelCapability.chat, ModelCapability.tool_calling])
        assert 'notes.read' in config.allowed_tools
        assert '不得改变用户指定的检查范围' in config.system_prompt
        rewrite_skill = install_zip((output / 'course-note-rewriter-1.0.0.zip').read_bytes(), 'skill', tmp_path / 'installed', runtime.skills.install)
        assert not rewrite_skill.missing_dependencies
        assert runtime.skills.enable('course-note-rewriter').status == 'ready'
        rewrite_config = runtime.skills.build_agent_configuration('course-note-rewriter', [ModelCapability.chat, ModelCapability.tool_calling])
        assert 'notes.create' in rewrite_config.allowed_tools
        assert 'markdown-workbench.inspect_markdown' in rewrite_config.allowed_tools
        assert '三道自测题' in rewrite_config.system_prompt
        runtime.plugins.disable('markdown-workbench')
        assert runtime.skills.get('note-reviewer').status == 'dependency_missing'
        assert runtime.skills.get('course-note-rewriter').status == 'dependency_missing'
    try:
        asyncio.run(run())
    finally:
        runtime.plugins.shutdown()


def test_study_plan_plugin_and_adaptive_skill_are_real_local_packages(tmp_path):
    builder = load(ROOT / 'build_packages.py')
    output = tmp_path / 'dist'
    catalog = builder.build(output)
    assert [item['id'] for item in catalog['packages'] if item['kind'] == 'plugin'] == [
        'markdown-workbench', 'study-plan-kit'
    ]
    assert {'course-note-rewriter', 'adaptive-study-coach'} <= {
        item['id'] for item in catalog['packages'] if item['kind'] == 'skill'
    }
    runtime = build_container()

    async def run():
        plugin = install_zip(
            (output / 'study-plan-kit-1.0.0.zip').read_bytes(),
            'plugin',
            tmp_path / 'installed',
            runtime.plugins.install,
        )
        assert not plugin.enabled
        skill = install_zip(
            (output / 'adaptive-study-coach-1.0.0.zip').read_bytes(),
            'skill',
            tmp_path / 'installed',
            runtime.skills.install,
        )
        assert 'study-plan-kit.build_sprint' in skill.missing_dependencies
        assert runtime.plugins.enable('study-plan-kit').status == 'ready'
        result = await runtime.tools.execute(
            ToolCall(
                tool_call_id='study-plan-test',
                name='study-plan-kit.build_sprint',
                arguments={
                    'topic': '三数和与头尾双指针',
                    'days': 7,
                    'daily_minutes': 45,
                    'confidence': 2,
                    'weak_points': '去重和指针移动条件',
                },
            ),
            ToolExecutionContext(run_id='study-plan-test'),
        )
        assert result.success, result.error_message
        assert result.output['constraints']['total_minutes'] == 315
        assert len(result.output['schedule']) == 7
        assert sum(result.output['phases'].values()) == 7
        assert result.output['constraints']['weak_points'] == '去重和指针移动条件'
        assert runtime.skills.enable('adaptive-study-coach').status == 'ready'
        config = runtime.skills.build_agent_configuration(
            'adaptive-study-coach', [ModelCapability.chat, ModelCapability.tool_calling]
        )
        assert 'tasks.create' in config.allowed_tools
        assert '严格保持工具返回的总分钟数' in config.system_prompt
        runtime.plugins.disable('study-plan-kit')
        assert runtime.skills.get('adaptive-study-coach').status == 'dependency_missing'

    try:
        asyncio.run(run())
    finally:
        runtime.plugins.shutdown()
