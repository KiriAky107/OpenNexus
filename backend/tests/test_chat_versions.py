from app.services import chat_history as history


def test_edits_regeneration_and_activity_survive_version_switch():
    history.create('Versions', 'versions')
    def append(id, role, content, parent=None, activity=None):
        history.append_message('versions', message_id=id, role=role, content=content, parent_message_id=parent, activity=activity)
    append('u1', 'user', 'original')
    append('a1', 'assistant', 'original answer', 'u1')
    append('u2', 'user', 'follow-up')
    append('a2', 'assistant', 'follow-up answer', 'u2')
    history.prepare_retry('versions', 'u1')
    append('u1-edit', 'user', 'edited')
    history.reserve_response('versions', 'a1-edit')
    trace = [{'type': 'thinking', 'text': 'before'}, {'type': 'tool', 'tool_call_id': 'tool'}, {'type': 'thinking', 'text': 'after'}]
    append('a1-edit', 'assistant', 'edited answer', 'u1-edit', trace)
    items, _ = history.list_messages('versions', 500, 0)
    assert [m.message_id for m in items] == ['u1-edit', 'a1-edit']
    assert items[0].versions == ['u1', 'u1-edit']
    assert items[1].activity == trace
    history.select_version('versions', 'u1')
    assert [m.message_id for m in history.list_messages('versions', 500, 0)[0]] == ['u1', 'a1', 'u2', 'a2']
    history.prepare_retry('versions', 'a1')
    history.reserve_response('versions', 'a1-new')
    append('a1-new', 'assistant', 'regenerated', 'u1')
    items, _ = history.list_messages('versions', 500, 0)
    assert [m.message_id for m in items] == ['u1', 'a1-new']
    assert items[-1].versions == ['a1', 'a1-new']
    history.select_version('versions', 'a1')
    assert history.list_messages('versions', 500, 0)[0][-1].message_id == 'a2'


def test_late_response_does_not_replace_new_generation():
    history.create('Late', 'late')
    history.append_message('late', message_id='u', role='user', content='question')
    history.reserve_response('late', 'new')
    history.append_message('late', message_id='old', role='assistant', content='old', parent_message_id='u')
    assert history.list_messages('late', 500, 0)[0][-1].message_id == 'u'
    history.append_message('late', message_id='new', role='assistant', content='new', parent_message_id='u')
    assert history.list_messages('late', 500, 0)[0][-1].message_id == 'new'


def test_workspace_snapshots_and_agent_links_survive_history_reload():
    history.create('Workspace', 'workspace')
    snapshot = {'file_path': 'demo.md', 'content': '# unsaved draft'}
    history.append_message('workspace', message_id='wu', role='user', content='explain', workspace_context=snapshot)
    calls = [{'tool_call_id': 'ac', 'name': 'agent.create', 'result': '{"run_id":"run_example"}'}]
    history.append_message('workspace', message_id='wa', role='assistant', content='started', tool_calls=calls)
    messages, total = history.list_messages('workspace', 100, 0)
    assert total == 2
    assert messages[0].workspace_context.model_dump() == snapshot
    assert messages[1].tool_calls == calls
