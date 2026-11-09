"""One source-bound native config intent on the original private file store.

No child is created, close permission consumed, or factory admitted here.
Object/source-buffer identity is not file immutability, original SDK/GUI/clock
adapter provenance, a security boundary or Native acceptance.
"""
import json
import re
import stat
import weakref

from android_release_sync_association_config_executable import OriginalAssociationConfigExecutable
from android_release_sync_association_config_resources import OriginalAssociationConfigResources
import android_release_sync_association_config_resources as resource_source
from android_release_sync_association_config_journals import OriginalAssociationConfigJournals
from android_release_sync_association_private_files import OriginalAssociationPrivateFiles, _ROLES
import android_release_sync_association_config_host as config

_ROLE = 'config-native-command-intent'
_ERROR = 'SIGNED_ASSOCIATION_CONFIG_NATIVE_INTENT_UNAVAILABLE'


class OriginalAssociationConfigNativeIntent:
    def __init__(self, resources, executable):
        self._origin = weakref.ref(self)
        self._state = 'NEW'
        self._entry = self._sources = self._clock_source = None
        self._require(type(resources) is OriginalAssociationConfigResources
            and type(executable) is OriginalAssociationConfigExecutable)
        self._resources, self._executable = resources, executable

    def _fail(self):
        self._state = 'FAILED'
        self._entry = self._sources = self._clock_source = None
        # Validation owns no fd. Only the actual store writer handles its errors.
        raise RuntimeError(_ERROR) from None

    def _require(self, value):
        if not value:
            self._fail()

    @staticmethod
    def _original(value):
        reference = getattr(value, '_origin', None)
        return type(reference) is weakref.ReferenceType and reference() is value

    def _pairs(self, pairs):
        self._require(len(pairs) <= 32 and all(type(k) is str for k, _ in pairs))
        self._require(len({k for k, _ in pairs}) == len(pairs))
        return dict(pairs)

    def _json(self, raw, limit):
        self._require(type(raw) is bytes and 0 < len(raw) <= limit)
        return json.loads(raw, object_pairs_hook=self._pairs, parse_constant=lambda _: self._fail())

    def _buffer(self):
        owner, executable = self._resources, self._executable
        self._require(self._original(owner) and self._original(executable)
            and owner._state == executable._state == 'READY'
            and resource_source._PRESPAWN_OWNERS.get(id(owner)) is owner)
        journals = owner._journals
        self._require(type(journals) is OriginalAssociationConfigJournals
            and self._original(journals) and journals._state == 'INTENT_RECORDED')
        store, entry = journals._store, journals._intent
        self._require(type(store) is OriginalAssociationPrivateFiles and store._state == 'READY'
            and type(store._entries) is dict and type(store._attempted) is set
            and all(type(k) is str for k in store._attempted)
            and _ROLE not in store._entries and _ROLE not in store._attempted
            and type(store._fds) is list and all(type(fd) is int and fd >= 0 for fd in store._fds)
            and type(entry) is tuple and len(entry) == 4
            and entry is store._entries.get('config-command-intent')
            and type(entry[0]) is bytes and 0 < len(entry[0]) <= 4096
            and type(entry[1]) is int and entry[1] in store._fds
            and type(entry[2]) is str and entry[2] == _ROLES['config-command-intent'][0]
            and type(entry[3]) is tuple and len(entry[3]) == 4
            and all(type(v) is int and v >= 0 for v in entry[3])
            and type(owner._buffers) is tuple and len(owner._buffers) == 2
            and owner._buffers[0] is entry[0] and owner._buffers[1] is owner._input)
        raw = self._json(entry[0], 4096)
        config.common.closed(raw, ('schema', 'launch_scope', 'command_id', 'arguments',
            'private_destination', 'stdin_bytes', 'automatic_retry'))
        scope = config.records.association_launch_scope(raw['launch_scope'])
        config.records._fresh(raw['command_id'], config.records._known_ids(scope))
        self._require(type(raw['schema']) is int and raw['schema'] == 1
            and type(raw['arguments']) is list and all(type(v) is str for v in raw['arguments'])
            and tuple(raw['arguments']) == config._ARGUMENTS
            and type(raw['private_destination']) is str and raw['private_destination'] == config._PATH
            and type(raw['stdin_bytes']) is int and 0 < raw['stdin_bytes'] <= 2048
            and raw['automatic_retry'] is False)
        stdin = self._json(owner._input, 2048)
        config.common.closed(stdin, ('vault_id', 'vault_name'))
        self._require(type(stdin['vault_id']) is str and stdin['vault_id'] == scope['remote_vault']
            and type(stdin['vault_name']) is str and re.fullmatch(r'Owned Android Sync [0-9a-f]{32}', stdin['vault_name']) is not None
            and raw['stdin_bytes'] == len(owner._input))
        self._require(type(owner._fds) is list and len(owner._fds) == 5
            and all(type(fd) is int and fd >= 3 for fd in owner._fds) and len(set(owner._fds)) == 5
            and not set(owner._fds).intersection(store._fds)
            and type(executable._fds) is list and 4 <= len(executable._fds) <= 35
            and all(type(fd) is int and fd >= 3 for fd in executable._fds)
            and len(set(executable._fds)) == len(executable._fds)
            and not set(executable._fds).intersection(owner._fds + store._fds)
            and type(executable._edges) is list and len(executable._edges) == len(executable._fds)
            and all(type(edge) is tuple and len(edge) == 5 and type(edge[2]) is int for edge in executable._edges)
            and [edge[2] for edge in executable._edges] == executable._fds)
        identity, fd = executable._edges[-1][3], executable._fds[-1]
        self._require(type(identity) is tuple and len(identity) == 8
            and all(type(v) is int and v >= 0 for v in identity)
            and stat.S_ISREG(identity[3]) and identity[4] == 1
            and type(executable._uid) is int and identity[2] == executable._uid
            and 20 <= identity[5] <= 128 * 1024 * 1024 and identity[3] & 0o100 and not identity[3] & 0o7022
            and type(executable._sdk) is str and 1 < len(executable._sdk) <= 512
            and type(executable._arguments) is tuple and all(type(v) is str for v in executable._arguments)
            and executable._arguments == (executable._sdk + '/platform-tools/adb', '-s', 'emulator-5562', *config._ARGUMENTS)
            and type(executable._parent_pid) is int and executable._parent_pid > 0
            and type(executable._executable) is str
            and executable._executable == '/proc/' + str(executable._parent_pid) + '/fd/' + str(fd)
            and type(owner._last) is int and 0 <= owner._last <= config.common.MAX
            and callable(owner._clock_source))
        # Every private profile above is closed before serialization or writing.
        value = dict(schema=1, config_command_id=raw['command_id'],
            source_intent_file=entry[2], native_executable_parent_fd_path=executable._executable,
            native_arguments=list(executable._arguments), parent_pid=executable._parent_pid,
            executable_fd=fd, executable_identity=list(identity), stdin_bytes=len(owner._input),
            source_host_observed_monotonic_ms=owner._last, automatic_retry=False)
        buffer = config.common.encoded(value)
        self._require(0 < len(buffer) <= 4096)
        stamp = (entry, tuple(owner._fds), tuple(executable._fds), executable._parent_pid,
            executable._executable, owner._last)
        return store, buffer, (entry[0], owner._input, executable._arguments), owner._clock_source, stamp

    def record(self):
        self._require(self._original(self) and self._state == 'NEW')
        self._state = 'WRITING'
        try:
            store, buffer, sources, clock, stamp = self._buffer()
            self._require(self._state == 'WRITING' and store.write(_ROLE, buffer) is None)
            self._require(self._state == 'WRITING' and store._state == 'READY')
            owner, executable = self._resources, self._executable
            self._require(owner._state == executable._state == 'READY'
                and resource_source._PRESPAWN_OWNERS.get(id(owner)) is owner
                and owner._journals._state == 'INTENT_RECORDED'
                and owner._journals._store is store and owner._journals._intent is stamp[0]
                and store._entries.get('config-command-intent') is stamp[0]
                and owner._input is sources[1] and executable._arguments is sources[2]
                and owner._clock_source is clock
                and type(owner._fds) is list and all(type(v) is int for v in owner._fds)
                and tuple(owner._fds) == stamp[1]
                and type(executable._fds) is list and all(type(v) is int for v in executable._fds)
                and tuple(executable._fds) == stamp[2]
                and type(executable._parent_pid) is int and executable._parent_pid == stamp[3]
                and type(executable._executable) is str and executable._executable == stamp[4]
                and type(owner._last) is int and owner._last == stamp[5])
            entry = store._entries.get(_ROLE)
            self._require(type(entry) is tuple and len(entry) == 4 and entry[0] is buffer
                and type(entry[1]) is int and entry[1] in store._fds
                and entry[1] not in self._resources._fds + self._executable._fds
                and type(entry[2]) is str and entry[2] == _ROLES[_ROLE][0]
                and type(entry[3]) is tuple and len(entry[3]) == 4
                and all(type(v) is int and v >= 0 for v in entry[3]))
            self._entry, self._sources, self._clock_source = entry, sources, clock
            self._state = 'RECORDED'
        except BaseException:
            self._fail()
