"""Read CPU counters for named threads in the verifier-owned WebView only."""
from __future__ import annotations

import ctypes as c
from ctypes import wintypes as w
import os
import time


class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
        ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
        ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
        ('name', w.WCHAR*260)]


class ThreadEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD),
        ('pid', w.DWORD), ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]


def ticks(value):
    return (value.dwHighDateTime << 32) | value.dwLowDateTime


class OwnedPreviewCpu:
    def __init__(self, process):
        if os.name != 'nt' or process.poll() is not None:
            raise RuntimeError('CPU measurement requires the live owned Windows Host')
        self.process = process
        self.kernel = k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateToolhelp32Snapshot': ([w.DWORD, w.DWORD], w.HANDLE),
            'Process32FirstW': ([w.HANDLE, c.POINTER(ProcessEntry)], w.BOOL),
            'Process32NextW': ([w.HANDLE, c.POINTER(ProcessEntry)], w.BOOL),
            'Thread32First': ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            'Thread32Next': ([w.HANDLE, c.POINTER(ThreadEntry)], w.BOOL),
            'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            'GetProcessIdOfThread': ([w.HANDLE], w.DWORD),
            'GetThreadDescription': ([w.HANDLE, c.POINTER(c.c_void_p)], w.LONG),
            'CloseHandle': ([w.HANDLE], w.BOOL),
            'LocalFree': ([c.c_void_p], c.c_void_p),
        }
        time_args = [w.HANDLE, *([c.POINTER(w.FILETIME)]*4)]
        signatures.update(GetProcessTimes=(time_args, w.BOOL), GetThreadTimes=(time_args, w.BOOL))
        for name, (args, result) in signatures.items():
            function = getattr(k, name); function.argtypes = args; function.restype = result
        self.handles = []
        self.selected = {}
        self.ancestry = []
        self.inventory = []
        try:
            self.root = self.open_process(process.pid)
            self.root_creation = self.read_times(self.root, thread=False)['creation_100ns']
        except BaseException:
            self.close(); raise

    def open_process(self, pid):
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise c.WinError(c.get_last_error())
        self.handles.append(handle)
        return handle

    def read_times(self, handle, *, thread):
        values = [w.FILETIME() for _ in range(4)]
        function = self.kernel.GetThreadTimes if thread else self.kernel.GetProcessTimes
        if not function(handle, *(c.byref(value) for value in values)):
            raise c.WinError(c.get_last_error())
        return {'creation_100ns':ticks(values[0]), 'kernel_100ns':ticks(values[2]), 'user_100ns':ticks(values[3])}

    def description(self, handle):
        value = c.c_void_p()
        if self.kernel.GetThreadDescription(handle, c.byref(value)) < 0:
            raise RuntimeError('Cannot read the owned WebView thread description')
        try:
            return c.wstring_at(value) if value.value else ''
        finally:
            if value.value:
                self.kernel.LocalFree(value)

    def snapshot(self, *, threads):
        handle = self.kernel.CreateToolhelp32Snapshot(4 if threads else 2, 0)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        entry = ThreadEntry() if threads else ProcessEntry()
        first = self.kernel.Thread32First if threads else self.kernel.Process32FirstW
        next_item = self.kernel.Thread32Next if threads else self.kernel.Process32NextW
        try:
            entry.size = c.sizeof(entry)
            available = first(handle, c.byref(entry)); rows = []
            while available:
                rows.append({'pid':entry.pid, 'tid':entry.tid} if threads else
                    {'pid':entry.pid, 'parent':entry.parent, 'name':entry.name})
                entry.size = c.sizeof(entry)
                available = next_item(handle, c.byref(entry))
            if c.get_last_error() not in (0, 18):
                raise c.WinError(c.get_last_error())
            return rows
        finally:
            self.kernel.CloseHandle(handle)

    def select(self):
        rows = {item['pid']:item for item in self.snapshot(threads=False)}
        creation = {self.process.pid:self.root_creation}
        allowed = {self.process.pid}
        for _ in range(len(rows)):
            additions = [item for item in rows.values() if item['parent'] in allowed and item['pid'] not in allowed]
            if not additions:
                break
            for item in additions:
                handle = self.open_process(item['pid'])
                born = self.read_times(handle, thread=False)['creation_100ns']
                if born < creation[item['parent']]:
                    raise RuntimeError('Process ancestry contains a reused parent PID')
                creation[item['pid']] = born; allowed.add(item['pid'])
                self.ancestry.append({**item, 'creation_100ns':born})
        webviews = {pid for pid in allowed if rows[pid]['name'].casefold()=='msedgewebview2.exe'}
        candidates = []
        for row in self.snapshot(threads=True):
            if row['pid'] not in webviews:
                continue
            handle = self.kernel.OpenThread(0x0800, False, row['tid'])
            if not handle:
                raise c.WinError(c.get_last_error())
            self.handles.append(handle)
            if self.kernel.GetProcessIdOfThread(handle) != row['pid']:
                raise RuntimeError('Thread owner changed during selection')
            name = self.description(handle)
            counters = self.read_times(handle, thread=True)
            if counters['creation_100ns'] < creation[row['pid']]:
                raise RuntimeError('Thread creation precedes its validated process')
            item = {**row, 'name':name, **counters}
            self.inventory.append(item)
            if name == 'DedicatedWorker thread':
                candidates.append((item, handle))
        if len(candidates) != 1:
            raise RuntimeError('Expected exactly one owned DedicatedWorker thread; found '+str(len(candidates)))
        worker, handle = candidates[0]
        self.selected['worker'] = (worker, handle)
        mains = [item for item in self.inventory if item['pid']==worker['pid'] and item['name']=='CrRendererMain']
        if len(mains) != 1:
            raise RuntimeError('Expected one main thread in the owned worker renderer')
        main = mains[0]
        main_handle = self.kernel.OpenThread(0x0800, False, main['tid'])
        if not main_handle:
            raise c.WinError(c.get_last_error())
        self.handles.append(main_handle)
        self.selected['main'] = (main, main_handle)

    def capture(self):
        if self.process.poll() is not None or self.read_times(self.root, thread=False)['creation_100ns'] != self.root_creation:
            raise RuntimeError('The verifier-owned Host is no longer live')
        result = {'monotonic_seconds':time.perf_counter()}
        for role, (original, handle) in self.selected.items():
            current = self.read_times(handle, thread=True)
            if self.kernel.GetProcessIdOfThread(handle) != original['pid'] or current['creation_100ns'] != original['creation_100ns'] or self.description(handle) != original['name']:
                raise RuntimeError('The selected WebView thread identity changed')
            result[role] = {**original, **current}
        return result

    def close(self):
        for handle in self.handles:
            self.kernel.CloseHandle(handle)
        self.handles.clear()
