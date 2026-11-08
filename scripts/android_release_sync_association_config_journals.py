"""Concrete mechanical config writers on the SAME private owning file store.

This supplies only two journal methods, not a config port adapter or admission.
The actual factory/history/driver/clock/managed command source remains required.
Object identity is neither a security boundary nor independent file readback.
"""
from android_release_sync_association_private_files import OriginalAssociationPrivateFiles, _ROLES

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_JOURNALS_UNAVAILABLE'
_INTENT = 'config-command-intent'
_RESULT = 'config-command-result'


class OriginalAssociationConfigJournals:
    def __init__(self, store):
        self._state = 'NEW'
        self._store = self._intent = self._result = None
        self._require(type(store) is OriginalAssociationPrivateFiles)
        self._store = store

    def _fail(self):
        self._state = 'FAILED'
        self._intent = self._result = None
        if self._store is not None:
            try:
                self._store.close()  # Only this same private owner's local fds.
            except Exception:
                pass
        raise RuntimeError(_ERROR) from None

    def _require(self, condition):
        if not condition:
            self._fail()

    def _entry(self, role, raw):
        self._require(self._store._state == 'READY')
        entry = self._store._entries.get(role)
        self._require(type(entry) is tuple and len(entry) == 4
            and entry[0] is raw and type(entry[1]) is int and entry[1] in self._store._fds
            and type(entry[2]) is str and entry[2] == _ROLES[role][0]
            and type(entry[3]) is tuple and len(entry[3]) == 4)
        return entry

    def _write(self, role, raw, writing_state):
        self._require(type(raw) is bytes and 0 < len(raw) <= 4096)
        self._require(self._store._state == 'READY')
        self._require(self._store.write(role, raw) is None)
        self._require(self._state == writing_state)
        return self._entry(role, raw)

    def record_config_intent(self, raw):
        self._require(self._state == 'NEW')
        self._state = 'WRITING_INTENT'
        try:
            self._intent = self._write(_INTENT, raw, 'WRITING_INTENT')
            self._state = 'INTENT_RECORDED'
        except Exception:
            self._fail()

    def record_config_result(self, raw):
        self._require(self._state == 'INTENT_RECORDED')
        self._state = 'WRITING_RESULT'
        try:
            self._require(self._intent is self._store._entries.get(_INTENT))
            self._require(self._entry(_INTENT, self._intent[0]) is self._intent)
            self._result = self._write(_RESULT, raw, 'WRITING_RESULT')
            self._state = 'DONE'
        except Exception:
            self._fail()
