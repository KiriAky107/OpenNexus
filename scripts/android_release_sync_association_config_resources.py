"""Concrete pre-spawn ownership of original pipes, buffers and host clock.

This class cannot launch or register a child. Its explicit local-fd release is
valid ONLY before spawning. A future kernel must consume/revoke this permission
before any send and separately own unknown children. No factory/admission/ACK,
public raw getter, caller PID, process poll, reader or success adapter is added.
Object origins/identity are source discipline, not a security or syscall proof.
"""
import os
import threading
import time
import weakref

from android_release_sync_association_config_channels import OriginalAssociationConfigChannels
from android_release_sync_association_config_journals import OriginalAssociationConfigJournals
from android_release_sync_association_private_files import OriginalAssociationPrivateFiles, _ROLES

_ERROR = 'SIGNED_ASSOCIATION_CONFIG_RESOURCES_UNAVAILABLE'
_MAX = 2**63 - 1
_PRESPAWN_OWNERS = {}  # At most one strong owner; no weak cleanup/finalizer.
_LOCK = threading.Lock()


class OriginalAssociationConfigResources:
    def __init__(self, channels, journals):
        self._origin = weakref.ref(self)
        self._state = 'NEW'
        self._channels = self._journals = None
        self._fds, self._uncertain_close = [], []
        self._pipes, self._identities, self._input, self._buffers = (), {}, None, None
        self._owned = self._registered = self._releasing = False
        self._clock_source, self._last = time.monotonic_ns, None
        self._require(type(channels) is OriginalAssociationConfigChannels
            and type(journals) is OriginalAssociationConfigJournals)
        self._channels, self._journals = channels, journals

    def _issued(self):
        reference = getattr(self, '_origin', None)
        if type(reference) is not weakref.ReferenceType or reference() is not self:
            self._state = 'FAILED'
            self._fds, self._pipes, self._identities, self._input, self._buffers = [], (), {}, None, None
            self._owned = self._registered = False
            raise RuntimeError(_ERROR) from None

    def _drop(self):
        if not self._registered:
            return True
        if not _LOCK.acquire(False):
            return False
        try:
            if _PRESPAWN_OWNERS.get(id(self)) is not self:
                return False
            del _PRESPAWN_OWNERS[id(self)]
            self._registered = False
            return True
        finally:
            _LOCK.release()

    def _release(self):
        if self._releasing or getattr(self._channels, '_handle_owner', None) is not self:
            return
        self._releasing = True
        try:
            while self._fds:
                fd = self._fds.pop()  # Remove before close; never retry reused fd numbers.
                try:
                    if os.close(fd) is not None:
                        self._uncertain_close.append(fd)
                except BaseException:
                    self._uncertain_close.append(fd)
        finally:
            self._owned = self._releasing = False

    def _fail(self):
        self._state = 'FAILED'
        if not self._releasing:
            self._release()  # This class has no child; only pre-spawn own fds.
            if not self._uncertain_close:
                self._drop()
        self._input, self._pipes, self._identities, self._buffers = None, (), {}, None
        raise RuntimeError(_ERROR) from None

    def _require(self, value):
        if not value:
            self._fail()

    @staticmethod
    def _original(value):
        reference = getattr(value, '_origin', None)
        return type(reference) is weakref.ReferenceType and reference() is value

    def _reserve(self):
        self._require(_LOCK.acquire(False) is True)
        try:
            self._require(not _PRESPAWN_OWNERS)
            _PRESPAWN_OWNERS[id(self)] = self
            self._registered = True
        finally:
            _LOCK.release()

    def claim(self):
        self._issued()
        self._require(self._state == 'NEW')
        self._state = 'CLAIMING'
        try:
            channel, journals = self._channels, self._journals
            self._require(self._original(channel) and self._original(journals)
                and channel._state == 'READY' and channel._handle_owner is channel
                and journals._state == 'INTENT_RECORDED')
            store, entry = journals._store, journals._intent
            self._require(type(store) is OriginalAssociationPrivateFiles and store._state == 'READY'
                and type(store._entries) is dict and type(store._fds) is list
                and all(type(fd) is int for fd in store._fds)
                and type(entry) is tuple and len(entry) == 4
                and entry is store._entries.get('config-command-intent')
                and type(entry[0]) is bytes and 0 < len(entry[0]) <= 4096
                and type(entry[1]) is int and entry[1] in store._fds
                and type(entry[2]) is str and entry[2] == _ROLES['config-command-intent'][0]
                and type(entry[3]) is tuple and len(entry[3]) == 4
                and all(type(v) is int and v >= 0 for v in entry[3]))
            original_fds, original_pipes = channel._fds, channel._pipes
            original_input, original_ids = channel._input, channel._identities
            self._require(type(original_fds) is list and len(original_fds) == 5
                and all(type(fd) is int and fd >= 3 and fd not in store._fds for fd in original_fds)
                and len(set(original_fds)) == 5
                and type(original_pipes) is tuple and len(original_pipes) == 3
                and all(type(pair) is tuple and len(pair) == size for pair, size in zip(original_pipes, (1, 2, 2)))
                and all(type(fd) is int for pair in original_pipes for fd in pair)
                and [fd for pair in original_pipes for fd in pair] == original_fds
                and type(original_ids) is dict and all(type(fd) is int for fd in original_ids)
                and set(original_ids) == set(original_fds)
                and all(type(v) is tuple and len(v) == 5
                    and all(type(n) is int and n >= 0 for n in v) for v in original_ids.values())
                and type(original_input) is bytes and 0 < len(original_input) <= 2048)
            self._reserve()
            self._require(channel._consume_for_resources(self) is None)
            self._require(self._state == 'CLAIMING' and self._owned and channel._state == 'TRANSFERRED'
                and channel._handle_owner is self and len(self._fds) == 5
                and not channel._transfer_reentered and not channel._fds and channel._input is None
                and self._fds is original_fds and self._pipes is original_pipes
                and self._input is original_input and self._identities is original_ids)
            self._buffers = (entry[0], original_input)
            self._state = 'READY'
        except BaseException:
            self._fail()

    def monotonic_ms(self):
        self._issued()
        self._require(self._state in ('NEW', 'READY'))
        original_state = self._state
        try:
            value = self._clock_source()
            self._require(self._state == original_state and type(value) is int and value >= 0)
            value //= 1_000_000
            self._require(value <= _MAX and (self._last is None or self._last <= value))
            self._last = value
            return value
        except BaseException:
            self._fail()

    def close(self):
        self._issued()
        if self._state == 'CLOSED':
            return
        self._require(self._state in ('NEW', 'READY'))
        self._state = 'CLOSING'
        self._release()
        self._require(self._state == 'CLOSING' and not self._uncertain_close and self._drop())
        self._input, self._pipes, self._identities, self._buffers = None, (), {}, None
        self._state = 'CLOSED'
