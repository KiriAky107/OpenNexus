"""Synthetic approved-worker fixture, embedded in the opt-in native test."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MODE = None  # Replaced by the test; never used as a product execution input.


def filesystem_report():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    free = kernel.LocalFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_void_p

    def access(path, rights):
        handle = create(str(path), rights, 7, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value:
            return ctypes.get_last_error()
        assert close(handle)
        return 0

    def rewrite_same_acl(path):
        # On owned synthetic objects only. Even an unexpected success leaves
        # the identical descriptor, preserving Host cleanup permissions.
        get = advapi.GetNamedSecurityInfoW
        get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        get.restype = wintypes.DWORD
        set_acl = advapi.SetNamedSecurityInfoW
        set_acl.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        set_acl.restype = wintypes.DWORD
        descriptor, acl = ctypes.c_void_p(), ctypes.c_void_p()
        status = get(str(path), 1, 4, None, None, ctypes.byref(acl), None, ctypes.byref(descriptor))
        if status:
            raise ctypes.WinError(status)
        try:
            return set_acl(str(path), 1, 4, None, None, acl, None)
        finally:
            free(descriptor)

    root = Path.cwd()
    assert root.samefile(Path(os.environ['TEMP']))
    output = root / 'worker-output.txt'
    output.write_text('owned synthetic output', encoding='utf-8')
    assert output.read_text(encoding='utf-8') == 'owned synthetic output'
    nested = root / 'nested'
    nested.mkdir()
    deleted = nested / 'deleted.txt'
    deleted.write_text('delete positive control', encoding='utf-8')
    deleted.unlink()
    nested.rmdir()

    report = {'root': access(root, 0x60000), 'parent': access(root.parent, 0x60000),
              'created_file': access(output, 0x60000),
              'root_acl_change': rewrite_same_acl(root),
              'file_acl_change': rewrite_same_acl(output),
              'outside_create': access(root.parent, 0x2),
              'scratch_read_write_delete': True, 'cwd_is_scratch': True}

    # Creation can supply an explicit protected descriptor. Audit this Windows
    # behavior honestly; this is not a promise that every child DACL is sealed.
    # Clone the current descriptor (Host/SYSTEM grants intact) so the test never
    # creates an object whose cleanup needs elevation or touches another profile.
    get = advapi.GetNamedSecurityInfoW
    get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    get.restype = wintypes.DWORD
    descriptor = ctypes.c_void_p()
    status = get(str(output), 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if status:
        raise ctypes.WinError(status)
    class Attributes(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p),
                    ('inherit', wintypes.BOOL)]
    attributes = Attributes(ctypes.sizeof(Attributes), descriptor.value, False)
    explicit = root / 'explicit-descriptor.txt'
    try:
        handle = create(str(explicit), 0x40000000, 7, ctypes.byref(attributes), 1, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        assert close(handle)
        report['explicit_descriptor_acl_access'] = access(explicit, 0x60000)
        explicit.unlink()
        token = wintypes.HANDLE()
        open_token = advapi.OpenProcessToken
        open_token.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        open_token.restype = wintypes.BOOL
        if not open_token(ctypes.c_void_p(-1), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            query = advapi.GetTokenInformation
            query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                              wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            query.restype = wintypes.BOOL
            buffer, length = ctypes.create_string_buffer(4096), wintypes.DWORD()
            if not query(token, 31, buffer, len(buffer), ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            package_sid = ctypes.c_void_p.from_buffer(buffer).value
            class Trustee(ctypes.Structure):
                _fields_ = [('multiple', ctypes.c_void_p), ('operation', wintypes.DWORD),
                            ('form', wintypes.DWORD), ('kind', wintypes.DWORD), ('name', ctypes.c_void_p)]
            class Entry(ctypes.Structure):
                _fields_ = [('permissions', wintypes.DWORD), ('mode', wintypes.DWORD),
                            ('inheritance', wintypes.DWORD), ('trustee', Trustee)]
            old_acl, present, defaulted = ctypes.c_void_p(), wintypes.BOOL(), wintypes.BOOL()
            get_dacl = advapi.GetSecurityDescriptorDacl
            get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
            get_dacl.restype = wintypes.BOOL
            if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(old_acl), ctypes.byref(defaulted)):
                raise ctypes.WinError(ctypes.get_last_error())
            assert present.value and old_acl.value
            entry = Entry(0x1f01ff, 2, 0, Trustee(None, 0, 0, 0, package_sid))
            new_acl = ctypes.c_void_p()
            merge = advapi.SetEntriesInAclW
            merge.argtypes = [wintypes.ULONG, ctypes.POINTER(Entry), ctypes.c_void_p,
                              ctypes.POINTER(ctypes.c_void_p)]
            merge.restype = wintypes.DWORD
            status = merge(1, ctypes.byref(entry), old_acl, ctypes.byref(new_acl))
            if status:
                raise ctypes.WinError(status)
            try:
                absolute = ctypes.create_string_buffer(64)
                initialize = advapi.InitializeSecurityDescriptor
                initialize.argtypes = [ctypes.c_void_p, wintypes.DWORD]
                initialize.restype = wintypes.BOOL
                set_dacl = advapi.SetSecurityDescriptorDacl
                set_dacl.argtypes = [ctypes.c_void_p, wintypes.BOOL, ctypes.c_void_p, wintypes.BOOL]
                set_dacl.restype = wintypes.BOOL
                control = advapi.SetSecurityDescriptorControl
                control.argtypes = [ctypes.c_void_p, wintypes.WORD, wintypes.WORD]
                control.restype = wintypes.BOOL
                assert initialize(absolute, 1)
                assert set_dacl(absolute, True, new_acl, False)
                assert control(absolute, 0x1000, 0x1000)
                custom = root / 'protected-descriptor.txt'
                attributes.descriptor = ctypes.addressof(absolute)
                handle = create(str(custom), 0x40000000, 7, ctypes.byref(attributes), 1, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                assert close(handle)
                report['protected_descriptor_acl_access'] = access(custom, 0x60000)
                # Leave this safe empty object for the actual Host cleanup, to
                # prove it is not forgotten. Host/SYSTEM grants were preserved.
            finally:
                free(new_acl)
        finally:
            assert close(token)
    finally:
        free(descriptor)
    return report


if sys.argv[1:] == ['child']:
    print('child-ready', flush=True)
    time.sleep(60)
    sys.exit(0)
assert len(sys.argv) == 1 and sys.flags.isolated == 1
assert 'OPENNEXUS_PROBE_PARENT_TOKEN' not in os.environ
if MODE in ('cancel', 'switch', 'shutdown'):
    subprocess.Popen([sys.executable, '-I', '-B', '-X', 'utf8', __file__, 'child'],
                     stdout=sys.stdout, stderr=sys.stderr, close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    time.sleep(60)
elif MODE == 'wall':
    time.sleep(60)
elif MODE == 'cpu':
    while True:
        pass
elif MODE == 'nonzero':
    print('real-failure', flush=True)
    sys.exit(7)
elif MODE == 'logs':
    sys.stdout.buffer.write(b'A' * (2 * 1024 * 1024))
    sys.stdout.flush()
    sys.stderr.buffer.write(b'E' * (2 * 1024 * 1024))
    sys.stderr.flush()
elif MODE == 'bad-output':
    (Path.cwd() / 'bad.json').write_bytes(b'{broken json')
elif MODE == 'named-stream':
    (Path.cwd() / 'output.txt').write_bytes(b'visible')
    (Path.cwd() / 'output.txt:hidden').write_bytes(b'hidden')
elif MODE == 'png-output':
    import struct
    import zlib
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00'))
    png += chunk(b'IEND', b'')
    (Path.cwd() / 'result.png').write_bytes(png)
    print('generated-png', flush=True)
else:
    scratch_acl_access = filesystem_report()
    rows = list(csv.DictReader(io.StringIO((Path(__file__).parent / 'input.csv').read_text(encoding='utf-8'))))
    assert sum(int(row['value']) for row in rows) == 18 and rows[0]['name'] == '中文'
    try:
        (Path(__file__).parent / 'input.csv').write_bytes(b'changed')
    except PermissionError:
        input_write_denied = True
    else:
        raise RuntimeError('input was writable')
    print('NATIVE_WORKER:中文:18:' + sys.version.split()[0], flush=True)
    print('WORKER_REPORT:' + json.dumps({
        'executable': sys.executable, 'runtime': sys.version.split()[0],
        'isolated': sys.flags.isolated, 'argv': sys.argv[1:],
        'parent_environment_inherited': 'OPENNEXUS_PROBE_PARENT_TOKEN' in os.environ,
        'input_write_denied': input_write_denied,
        'csv_total': sum(int(row['value']) for row in rows), 'first_name': rows[0]['name'],
        'scratch_acl_access': scratch_acl_access}), flush=True)
