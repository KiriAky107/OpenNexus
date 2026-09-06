"""One persistent persona for all configured chat/agent providers on this AI Core."""
from contextlib import closing
from pydantic import BaseModel, ConfigDict, Field
from app.database.db import connect


class DialoguePair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: str = Field(default="", max_length=8000)
    assistant: str = Field(default="", max_length=8000)


class PersonaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=0, ge=0)
    name: str = Field(default="", max_length=128)
    system_prompt: str = Field(default="", max_length=16000)
    dialogue_pairs: list[DialoguePair] = Field(default_factory=list, max_length=20)


def connection():
    conn = connect()
    conn.execute("CREATE TABLE IF NOT EXISTS global_persona (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
    return conn


def load_persona():
    with closing(connection()) as conn:
        row = conn.execute("SELECT data FROM global_persona WHERE id=1").fetchone()
        return PersonaSettings.model_validate_json(row[0]) if row else PersonaSettings()


def save_persona(settings):
    from app.errors import ApiError
    with closing(connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT data FROM global_persona WHERE id=1").fetchone()
            current = PersonaSettings.model_validate_json(row[0]) if row else PersonaSettings()
            if current.version != settings.version:
                raise ApiError(409, "PERSONA_VERSION_CONFLICT", "全局人设已被修改，请重新打开表单后保存。")
            updated = settings.model_copy(update={"version": current.version + 1})
            conn.execute("INSERT OR REPLACE INTO global_persona(id,data) VALUES(1,?)", (updated.model_dump_json(),))
            conn.commit()
            return updated
        except BaseException:
            conn.rollback()
            raise


def apply_global_persona(request):
    settings = load_persona()
    parts = [request.system or ""]
    if settings.system_prompt.strip():
        parts.append("全局人设 / Global persona\n" + settings.system_prompt.strip())
    examples = []
    for pair in settings.dialogue_pairs:
        lines = []
        if pair.user.strip(): lines.append("User: " + pair.user.strip())
        if pair.assistant.strip(): lines.append("Assistant: " + pair.assistant.strip())
        if lines: examples.append("\n".join(lines))
    if examples:
        parts.append("预设对话示例 / Example dialogue\n" + "\n\n".join(examples))
    system = "\n\n".join(part for part in parts if part.strip())
    return request.model_copy(update={"system": system or None})
