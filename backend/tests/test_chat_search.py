import asyncio
import json
from uuid import uuid4
import pytest
from app.services import chat_history, chat_search
from app.errors import ApiError
from app.database.db import connect_knowledge


def add(conversation, id, content, **kwargs):
    chat_history.append_message(conversation, message_id=id, role=kwargs.pop('role', 'assistant'), content=content, **kwargs)


def test_search_indexes_visible_text_and_actual_tool_summaries_without_reasoning_or_arguments():
    c = chat_history.create('test').conversation_id
    add(c, 'u', '用户问题 检索', role='user')
    add(c, 'a', 'firstlast', thinking='private reasoning', activity=[{'type': 'thinking', 'text': 'secret thought'}, {'type': 'text', 'text': 'first'}, {'type': 'tool', 'tool_call_id': 't'}, {'type': 'text', 'text': 'last'}],
        tool_calls=[{'tool_call_id': 't', 'name': 'rag.search', 'status': 'completed', 'parameters': {'secret': 'hidden parameter'}, 'result': json.dumps({'title': 'Orchard', 'raw': 'hidden return'})}])
    assert chat_search.search(c, '用户')["items"][0]['message_id'] == 'u'
    assert chat_search.search(c, 'first')["items"][0]['message_id'] == 'a'
    assert chat_search.search(c, 'last')["items"][0]['entry_index'] == 2
    assert chat_search.search(c, '检索知识库')["items"][0]['tool_call_id'] == 't'
    assert chat_search.search(c, 'Orchard')["items"][0]['kind'] == 'tool'
    for query in ['private reasoning', 'secret thought', 'hidden parameter', 'hidden return', 'firstlast']:
        assert chat_search.search(c, query)['items'] == []


def test_search_preserves_all_answer_attempts_and_survives_reopen_and_pagination():
    c = chat_history.create('test').conversation_id
    add(c, 'u', 'question', role='user')
    add(c, 'a1', 'needle first')
    add(c, 'a2', 'needle second', parent_message_id='u')
    result = chat_search.search(c, 'needle', limit=1)
    assert result['has_more'] and result['items'][0]['message_id'] == 'a1'
    assert chat_search.search(c, 'needle', limit=1, offset=1)['items'][0]['message_id'] == 'a2'
    assert not chat_search.search(c, 'needle', limit=1, offset=1)['has_more']
    add(c, 'u2', 'needle later', role='user')
    assert len(chat_search.search(c, 'needle')['items']) == 3
    conn = connect_knowledge()
    try:
        assert conn.execute('SELECT COUNT(*) FROM chat_search_entries').fetchone()[0] == 4
    finally: conn.close()
    chat_history.delete(c)
    with pytest.raises(ApiError): chat_search.search(c, 'needle')
    conn = connect_knowledge()
    try: assert conn.execute('SELECT COUNT(*) FROM chat_search_entries').fetchone()[0] == 0
    finally: conn.close()


def test_search_literal_punctuation_unicode_and_legacy_text_are_safe():
    c = chat_history.create('test').conversation_id
    add(c, 'a', 'a%_b "quoted" 中文测试needle')
    for query in ['%', '_', 'a%_b', '"quoted"', '中文测', 'NEEDLE']:
        assert chat_search.search(c, query)['items'], query
    assert not chat_search.search(c, 'other')['items']


def test_search_respects_vault_scope(monkeypatch):
    from app.config import get_settings
    from app import host_bridge
    monkeypatch.setenv('APP_ENVIRONMENT', 'desktop'); get_settings.cache_clear()
    one = host_bridge.vault_id.set(str(uuid4()))
    try:
        c = chat_history.create('one').conversation_id; add(c, 'a', 'needle')
        assert chat_search.search(c, 'needle')['items']
        two = host_bridge.vault_id.set(str(uuid4()))
        try:
            with pytest.raises(ApiError): chat_search.search(c, 'needle')
        finally: host_bridge.vault_id.reset(two)
    finally: host_bridge.vault_id.reset(one)
