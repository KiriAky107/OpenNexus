"""Device-local permission to propose experiments; never execution consent."""
from contextlib import closing
import json
import sqlite3
from threading import RLock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.permissions import PermissionMode, PermissionPolicy
from app.database.db import connect, transaction
from app.errors import ApiError

PERMISSIONS = ('experiments.run', 'experiments.import')
LOCK = RLock()


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    permission: Literal['experiments.run', 'experiments.import']
    mode: Literal['confirm', 'deny']
    expected_revision: int = Field(ge=0)


def _read(conn):
    row = conn.execute('SELECT revision,rules FROM experiment_permission_policy WHERE singleton=1').fetchone()
    if row is None:
        return {'revision': 0, 'rules': {key: 'deny' for key in PERMISSIONS}}
    try:
        if type(row['revision']) is not int or row['revision'] < 1 or len(row['rules']) > 1024:
            raise ValueError()
        rules = json.loads(row['rules'])
        if not isinstance(rules, dict) or set(rules) != set(PERMISSIONS) or any(mode not in ('deny', 'confirm') for mode in rules.values()):
            raise ValueError()
    except (TypeError, ValueError):
        raise ApiError(409, 'EXPERIMENT_PERMISSION_SETTINGS_INVALID', '实验权限设置无法核对，运行和导入保持拒绝。原设置已保留。') from None
    return {'revision': row['revision'], 'rules': rules}


def read():
    with LOCK, closing(connect()) as conn:
        return _read(conn)


def restore(policy: PermissionPolicy):
    with LOCK:
        try:
            state = read()
        except (ApiError, sqlite3.Error):
            # An unreadable device preference never enables proposals on startup.
            return
        for key, mode in state['rules'].items():
            policy.set_rule(key, PermissionMode(mode))


def update(request: Update, policy: PermissionPolicy):
    with LOCK:
        with closing(connect()) as conn, transaction(conn, immediate=True):
            state = _read(conn)
            if request.expected_revision != state['revision']:
                raise ApiError(409, 'EXPERIMENT_PERMISSION_SETTINGS_CHANGED', '权限设置已变化，请刷新后重新选择。')
            rules = {**state['rules'], request.permission: request.mode}
            revision = state['revision'] + 1
            conn.execute('INSERT INTO experiment_permission_policy(singleton,revision,rules) VALUES(1,?,?) '
                         'ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,rules=excluded.rules',
                         (revision, json.dumps(rules, sort_keys=True)))
        # Keep the lock through publication: concurrent saves cannot leave the
        # live policy at an older revision than the committed device preference.
        for key, mode in rules.items():
            policy.set_rule(key, PermissionMode(mode))
        return {'revision': revision, 'rules': rules}
