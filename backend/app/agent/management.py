"""Versioned Agent definitions and reviewed, bounded collaboration plans."""
from contextlib import closing
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import host_bridge
from app.database.db import connect, transaction
from app.errors import ApiError


def scope() -> str:
    return host_bridge.vault_id.get() or ''


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DefinitionConfig(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default='', max_length=1000)
    instructions: str = Field(default='', max_length=12000)
    provider_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=200)
    tools: list[str] = Field(default_factory=list, max_length=20)
    token_budget: int | None = Field(default=None, ge=1, le=1000000)
    max_steps: int = Field(default=10, ge=1, le=30)
    enabled: bool = True

    @model_validator(mode='after')
    def bounded_tools(self):
        if len(set(self.tools)) != len(self.tools) or any(name.startswith('agent.') for name in self.tools):
            raise ValueError('Duplicate tools or recursive delegation are not allowed')
        if not self.name.strip():
            raise ValueError('Name cannot be blank')
        return self


class DefinitionWrite(StrictModel):
    config: DefinitionConfig
    expected_revision: int | None = Field(default=None, ge=1)


class Member(StrictModel):
    member_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    agent_id: str
    input: str = Field(min_length=1, max_length=16000)
    depends_on: list[str] = Field(default_factory=list, max_length=5)


class CollaborationPlan(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    members: list[Member] = Field(min_length=1, max_length=6)
    max_concurrency: int = Field(default=2, ge=1, le=3)
    token_budget: int = Field(default=24000, ge=1, le=1000000)
    max_tool_calls: int = Field(default=60, ge=1, le=200)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @model_validator(mode='after')
    def dag(self):
        graph = {member.member_id: member.depends_on for member in self.members}
        if len(graph) != len(self.members):
            raise ValueError('Duplicate member identifiers')
        visited, visiting = set(), set()
        def visit(node):
            if node not in graph or node in visiting:
                raise ValueError('Dependency cycle or unknown dependency')
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)
        for member in graph:
            visit(member)
        return self


class StartRequest(StrictModel):
    input: str = Field(min_length=1, max_length=16000)
    operation_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class ReviewRequest(StrictModel):
    expected_revision: int = Field(ge=1)
    decision: Literal['approve', 'reject']


def now():
    return datetime.now(timezone.utc).isoformat()


class ManagementStore:
    def review_change(self, identifier, request, runtime):
        # Commit the definition and the approval receipt together. A disconnect
        # cannot leave an applied edit looking pending on another page.
        with closing(connect()) as conn, transaction(conn, immediate=True):
            row = conn.execute("SELECT data FROM agent_objects WHERE scope=? AND kind='change' AND id=?", (scope(), identifier)).fetchone()
            if not row:
                raise ApiError(404, 'AGENT_OBJECT_NOT_FOUND', 'Change not found')
            change = json.loads(row['data'])
            if change['status'] != 'pending':
                return change
            if change['revision'] != request.expected_revision:
                raise ApiError(409, 'AGENT_REVISION_CONFLICT', 'Review the current change.')
            if request.decision == 'approve':
                row = conn.execute("SELECT data FROM agent_objects WHERE scope=? AND kind='definition' AND id=?", (scope(), change['agent_id'])).fetchone()
                definition = json.loads(row['data']) if row else None
                if not definition or definition['revision'] != change['expected_revision']:
                    raise ApiError(409, 'AGENT_REVISION_CONFLICT', 'The Agent changed; propose a new edit.')
                if change['action'] == 'delete':
                    conn.execute("DELETE FROM agent_objects WHERE scope=? AND kind='definition' AND id=?", (scope(), definition['id']))
                else:
                    config = DefinitionConfig.model_validate(change['config'])
                    validate_config(config, runtime)
                    definition.update(config=config.model_dump(), revision=definition['revision'] + 1, updated_at=now())
                    conn.execute("UPDATE agent_objects SET data=?,updated_at=? WHERE scope=? AND kind='definition' AND id=?",
                        (json.dumps(definition, ensure_ascii=False), definition['updated_at'], scope(), definition['id']))
            change.update(status='approved' if request.decision == 'approve' else 'rejected', revision=change['revision'] + 1, updated_at=now())
            conn.execute("UPDATE agent_objects SET data=?,updated_at=? WHERE scope=? AND kind='change' AND id=?",
                (json.dumps(change, ensure_ascii=False), change['updated_at'], scope(), identifier))
            return change

    def get(self, kind: str, identifier: str) -> dict:
        with closing(connect()) as conn:
            row = conn.execute('SELECT data FROM agent_objects WHERE scope=? AND kind=? AND id=?', (scope(), kind, identifier)).fetchone()
        if not row:
            raise ApiError(404, 'AGENT_OBJECT_NOT_FOUND', 'Agent object does not exist in this knowledge base.')
        return json.loads(row['data'])

    def list(self, kind: str, conversation_id: str | None = None) -> list[dict]:
        with closing(connect()) as conn:
            rows = conn.execute('SELECT data FROM agent_objects WHERE scope=? AND kind=? ORDER BY updated_at DESC LIMIT 200', (scope(), kind)).fetchall()
        items = [json.loads(row['data']) for row in rows]
        return [item for item in items if conversation_id is None or item.get('conversation_id') == conversation_id]

    def insert(self, kind: str, data: dict, operation_id: str | None = None) -> dict:
        with closing(connect()) as conn, transaction(conn, immediate=True):
            if operation_id:
                existing = conn.execute('SELECT data FROM agent_objects WHERE scope=? AND kind=? AND operation_id=?', (scope(), kind, operation_id)).fetchone()
                if existing:
                    return json.loads(existing['data'])
            data = {**data, 'id': f'{kind}_{uuid4().hex}', 'revision': 1, 'created_at': now(), 'updated_at': now()}
            conn.execute('INSERT INTO agent_objects(scope,kind,id,operation_id,data,updated_at) VALUES(?,?,?,?,?,?)',
                         (scope(), kind, data['id'], operation_id, json.dumps(data, ensure_ascii=False), data['updated_at']))
        return data

    def save(self, kind: str, data: dict, expected_revision: int) -> dict:
        with closing(connect()) as conn, transaction(conn, immediate=True):
            row = conn.execute('SELECT data FROM agent_objects WHERE scope=? AND kind=? AND id=?', (scope(), kind, data['id'])).fetchone()
            if not row or json.loads(row['data'])['revision'] != expected_revision:
                raise ApiError(409, 'AGENT_REVISION_CONFLICT', 'Configuration changed; reload and review the latest revision.')
            data = {**data, 'revision': expected_revision + 1, 'updated_at': now()}
            conn.execute('UPDATE agent_objects SET data=?,updated_at=? WHERE scope=? AND kind=? AND id=?',
                         (json.dumps(data, ensure_ascii=False), data['updated_at'], scope(), kind, data['id']))
        return data

    def delete(self, identifier: str, revision: int):
        with closing(connect()) as conn, transaction(conn, immediate=True):
            row = conn.execute("SELECT data FROM agent_objects WHERE scope=? AND kind='definition' AND id=?", (scope(), identifier)).fetchone()
            if not row or json.loads(row['data'])['revision'] != revision:
                raise ApiError(409, 'AGENT_REVISION_CONFLICT', 'Reload the definition before deleting it.')
            conn.execute("DELETE FROM agent_objects WHERE scope=? AND kind='definition' AND id=?", (scope(), identifier))


store = ManagementStore()


def validate_config(config: DefinitionConfig, runtime):
    from app.contracts import ModelCapability
    provider = runtime.providers.get(config.provider_id)
    if ModelCapability.tool_calling not in provider.config.capabilities:
        raise ApiError(400, 'AGENT_TOOL_CALLING_REQUIRED', 'The selected model provider does not support tool calling.')
    for name in config.tools:
        try:
            definition = runtime.tools.get(name).definition
        except LookupError:
            raise ApiError(400, 'AGENT_TOOL_UNAVAILABLE', f'Tool is unavailable: {name}') from None
        if definition.permission == 'network.request':
            raise ApiError(403, 'AGENT_NETWORK_DISABLED', 'Network tools are not enabled for managed agents.')


def create_definition(config: DefinitionConfig, runtime, operation_id=None, *, origin='manual'):
    validate_config(config, runtime)
    if origin not in {'manual', 'chat', 'benchmark'}:
        raise ValueError('Invalid definition origin')
    return store.insert('definition', {'config': config.model_dump(), 'origin': origin}, operation_id)


def update_definition(identifier: str, request: DefinitionWrite, runtime):
    existing = store.get('definition', identifier)
    validate_config(request.config, runtime)
    return store.save('definition', {**existing, 'config': request.config.model_dump()}, request.expected_revision or 0)
