from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.contracts import ModelEvent, ModelEventType
from app.main import app
from app.services import chat_history


def test_chat_history_survives_new_connections_and_deletes_messages() -> None:
    conversation = chat_history.create("Persistent chat", "conversation-1")
    chat_history.append_message(
        conversation.conversation_id,
        message_id="user-1",
        role="user",
        content="question",
    )
    chat_history.append_message(
        conversation.conversation_id,
        message_id="assistant-1",
        role="assistant",
        content="answer",
        citations=[{"note_id": "note-1", "heading_path": ["Heading"]}],
        usage={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    )

    listed, total = chat_history.list_conversations(50, 0)
    messages, message_total = chat_history.list_messages("conversation-1", 50, 0)
    assert total == 1
    assert listed[0].message_count == 2
    assert message_total == 2
    assert messages[1].citations[0]["note_id"] == "note-1"
    assert messages[1].usage["total_tokens"] == 3

    assert chat_history.delete("conversation-1") is True
    assert chat_history.list_conversations(50, 0)[1] == 0


def test_chat_stream_persists_user_and_assistant_messages(monkeypatch) -> None:
    from app import routes

    class Adapter:
        async def stream(self, _request):
            now = datetime.now(timezone.utc)
            yield ModelEvent(event=ModelEventType.text_delta, data={"text": "persisted answer"}, timestamp=now)
            yield ModelEvent(event=ModelEventType.usage, data={"input_tokens": 4, "output_tokens": 2}, timestamp=now)
            yield ModelEvent(event=ModelEventType.done, timestamp=now)

    monkeypatch.setattr(routes, "provider_or_404", lambda _provider_id: SimpleNamespace(adapter=Adapter()))
    payload = {
        "provider_id": "configured",
        "model": "model",
        "conversation_id": "conversation-stream",
        "user_message_id": "user-stream",
        "assistant_message_id": "assistant-stream",
        "conversation_title": "Persist this",
        "use_rag": False,
        "messages": [{"role": "user", "content": "question"}],
    }
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json=payload) as response:
            assert response.status_code == 200
            assert "persisted answer" in "".join(response.iter_text())
        messages = client.get("/api/chat/conversations/conversation-stream/messages").json()["items"]
        conversations = client.get("/api/chat/conversations").json()["items"]
        assert [message["content"] for message in messages] == ["question", "persisted answer"]
        assert messages[1]["usage"]["total_tokens"] == 6
        assert conversations[0]["title"] == "Persist this"
        assert conversations[0]["message_count"] == 2


def test_chat_conversation_crud_api() -> None:
    with TestClient(app) as client:
        created = client.post("/api/chat/conversations", json={"conversation_id": "crud", "title": "CRUD"})
        assert created.status_code == 201
        assert client.get("/api/chat/conversations").json()["page"]["total"] == 1
        assert client.get("/api/chat/conversations/crud/messages").json()["items"] == []
        assert client.delete("/api/chat/conversations/crud").status_code == 200
        missing = client.get("/api/chat/conversations/crud/messages")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"
