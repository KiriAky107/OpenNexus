"""Consume original SDK close authority into pre-child host ownership.

This is a descriptor owner and original clock source, not a managed ADB server,
environment attestation, child registry, factory admission or command adapter.
Unknown states retain both strong owners; teardown never polls or closes them.
"""
import os
import sys
import threading
import time
import weakref

import android_release_preconfig_sdk as sdk_source

_ERROR = 'SIGNED_PRECONFIG_HOST_RESOURCES_UNAVAILABLE'
_MAX = (1 << 63) - 1
_OWNERS = {}
_LOCK = threading.Lock()


class OriginalPreconfigHostResources:
    def __init__(self, sdk):
        self._origin = weakref.ref(self)
        self._handle_owner = self
        self._state, self._registered = 'NEW', False
        self._sdk = None
        self._fds, self._edges, self._aliases, self._uncertain = [], [], [], []
        self._sources = self._stamp = self._last = self._close = None
        self._clock_source = time.monotonic_ns  # Capture, never sample here.
        self._need(type(sdk) is sdk_source.OriginalPreconfigSdk)
        self._sdk = sdk

    @staticmethod
    def _original(value):
        reference = getattr(value, '_origin', None)
        return type(reference) is weakref.ReferenceType and reference() is value

    def _issued(self):
        if not self._original(self):
            self._state, self._registered = 'FAILED', False
            self._fds, self._edges, self._aliases = [], [], []
            raise RuntimeError(_ERROR) from None
        if self._handle_owner is not self:
            raise RuntimeError(_ERROR) from None

    def _reserve(self):
        self._need(_LOCK.acquire(False) is True)
        try:
            self._need(not _OWNERS)
            _OWNERS[id(self)] = self
            self._registered = True
        finally:
            _LOCK.release()

    def _drop(self):
        if not self._registered:
            return True
        if not _LOCK.acquire(False):
            return False
        try:
            if _OWNERS.get(id(self)) is not self:
                return False
            del _OWNERS[id(self)]
            self._registered = False
            return True
        finally:
            _LOCK.release()

    def _fail(self):
        self._state = 'FAILED'
        captured = type(self._sources) is tuple and len(self._sources) == 5 and self._fds is self._sources[0]
        if not captured and (self._sdk is None or getattr(self._sdk, '_handle_owner', None) is not self):
            self._drop()  # Never close descriptors still owned by the SDK source.
        # Once consumed, retain original known/uncertain handles without cleanup.
        raise RuntimeError(_ERROR) from None

    def _need(self, value):
        if not value:
            self._fail()

    def _ready_source(self):
        sdk = self._sdk
        self._need(self._original(sdk) and type(sdk._state) is str and sdk._state == 'READY'
            and sdk._handle_owner is sdk and sdk._registered is True
            and sdk_source._OWNERS.get(id(sdk)) is sdk
            and type(sdk._inflight) is int and sdk._inflight == 0 and sdk._releasing is False
            and sdk._open_unknown is False and type(sdk._uncertain) is list and not sdk._uncertain
            and type(sdk._fds) is list and 3 <= len(sdk._fds) <= 40
            and all(type(v) is int and v >= 3 for v in sdk._fds)
            and len(set(sdk._fds)) == len(sdk._fds)
            and type(sdk._edges) is list and len(sdk._edges) == len(sdk._fds)
            and type(sdk._aliases) is list and len(sdk._aliases) == 2
            and type(sdk._context) is tuple and len(sdk._context) == 8
            and all(type(v) is str for v in sdk._context[:7])
            and (sdk._context[7] is None or type(sdk._context[7]) is str)
            and type(sdk._sdk) is str and sdk._sdk == sdk._context[6]
            and type(sdk._parent_pid) is int and sdk._parent_pid > 0
            and type(sdk._uid) is int and sdk._uid >= 0)
        for edge in sdk._edges:
            self._need(type(edge) is tuple and len(edge) == 5
                and (edge[0] is None or type(edge[0]) is int and edge[0] in sdk._fds)
                and type(edge[1]) is str and type(edge[2]) is int and edge[2] in sdk._fds
                and type(edge[3]) is tuple and len(edge[3]) == 8
                and all(type(v) is int and v >= 0 for v in edge[3])
                and type(edge[4]) is str and edge[4] in ('directory', 'adb', 'api36'))
        self._need(tuple(e[2] for e in sdk._edges) == tuple(sdk._fds))
        for alias in sdk._aliases:
            self._need(type(alias) is tuple and len(alias) == 3
                and type(alias[0]) is str and type(alias[1]) is str
                and type(alias[2]) is tuple and len(alias[2]) == 8
                and all(type(v) is int and v >= 0 for v in alias[2]))

    def _snapshot(self):
        self._ready_source()
        sdk = self._sdk
        self._sources = (sdk._fds, sdk._edges, sdk._aliases, sdk._context, sdk._sdk)
        self._stamp = (tuple(sdk._fds), tuple(sdk._edges), tuple(sdk._aliases), sdk._parent_pid, sdk._uid)

    def _stable(self):
        self._ready_source()
        sdk = self._sdk
        self._need(self._state in ('CLAIMING', 'CONSUMING')
            and _OWNERS.get(id(self)) is self and self._registered is True
            and sdk._fds is self._sources[0] and sdk._edges is self._sources[1]
            and sdk._aliases is self._sources[2] and sdk._context is self._sources[3]
            and sdk._sdk is self._sources[4]
            and (tuple(sdk._fds), tuple(sdk._edges), tuple(sdk._aliases),
                sdk._parent_pid, sdk._uid) == self._stamp)

    def _owned(self):
        self._need(type(self._sources) is tuple and len(self._sources) == 5
            and type(self._stamp) is tuple and len(self._stamp) == 5)
        self._need(self._original(self._sdk) and self._registered is True
            and _OWNERS.get(id(self)) is self and self._sdk._handle_owner is self
            and type(self._sdk._state) is str and self._sdk._state == 'TRANSFERRED'
            and self._sdk._registered is True
            and sdk_source._OWNERS.get(id(self._sdk)) is self._sdk
            and self._sdk._context is self._sources[3] and self._sdk._sdk is self._sources[4]
            and type(self._sdk._parent_pid) is int and self._sdk._parent_pid == self._stamp[3]
            and type(self._sdk._uid) is int and self._sdk._uid == self._stamp[4]
            and type(self._sdk._fds) is list and not self._sdk._fds
            and type(self._sdk._edges) is list and not self._sdk._edges
            and type(self._sdk._aliases) is list and not self._sdk._aliases
            and self._fds is self._sources[0] and self._edges is self._sources[1]
            and self._aliases is self._sources[2] and not self._uncertain
            and all(type(v) is int and v >= 3 for v in self._fds)
            and tuple(self._fds) == self._stamp[0]
            and len(self._edges) == len(self._stamp[1])
            and all(a is b for a, b in zip(self._edges, self._stamp[1]))
            and len(self._aliases) == len(self._stamp[2])
            and all(a is b for a, b in zip(self._aliases, self._stamp[2]))
            and (self._last is None or type(self._last) is int and 0 <= self._last <= _MAX))

    def claim(self):
        self._issued()
        self._need(self._state == 'NEW')
        self._state = 'CLAIMING'
        try:
            self._need(sys.platform == 'linux' and os.name == 'posix' and os is sdk_source.os
                and callable(getattr(os, 'close', None)))
            self._snapshot()
            self._reserve()  # Strong original source refs before authority move.
            self._stable()
            self._close = os.close
            self._state = 'CONSUMING'
            self._need(self._sdk._consume_for_host(self) is None)
            self._owned()
            self._state = 'READY'
        except BaseException:
            self._fail()

    def monotonic_ms(self):
        self._issued()
        self._need(self._state == 'READY')
        self._owned()
        self._need(self._clock_source is time.monotonic_ns)
        self._state = 'CLOCK'
        try:
            ns = self._clock_source()
            self._need(self._state == 'CLOCK' and type(ns) is int
                and 0 <= ns <= _MAX * 1_000_000 + 999_999)
            self._owned()
            value = ns // 1_000_000
            self._need(self._last is None or value >= self._last)
            self._last, self._state = value, 'READY'
            return value
        except BaseException:
            self._fail()

    def close(self):
        self._issued()
        if self._state == 'CLOSED':
            return
        self._need(self._state in ('NEW', 'READY'))
        if self._state == 'NEW':
            self._state = 'CLOSED'
            return
        self._owned()
        self._state = 'CLOSING'
        original_fds = self._stamp[0]
        for index, fd in enumerate(reversed(original_fds)):
            self._need(self._state == 'CLOSING'
                and self._sdk._handle_owner is self and self._registered is True
                and _OWNERS.get(id(self)) is self
                and sdk_source._OWNERS.get(id(self._sdk)) is self._sdk
                and all(type(v) is int for v in self._fds)
                and tuple(self._fds) == original_fds[:len(original_fds) - index])
            self._fds.pop()
            try:
                known = self._close(fd) is None
            except BaseException:
                known = False
            if not known or self._state != 'CLOSING':
                self._uncertain.append(fd)
                self._fail()
        self._need(all(type(v) is int for v in self._fds) and not self._fds)
        try:
            self._need(self._sdk._retire_host_owner(self) is None)
            self._need(self._state == 'CLOSING' and self._drop())
            self._state = 'CLOSED'
        except BaseException:
            self._fail()
