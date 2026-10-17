"""Choose exactly one new owned vault through the actual hosted Windows dialog.

No recent-vault rows, native permissions or clipboard contents are fabricated.
Only the recorded OpenNexus PID's named folder dialog and standard controls may
receive input. The functions are not a general desktop automation interface.
"""
import os
import time

from android_desktop_handoff import canonical, require

TITLE = '选择本地 Vault'


def native_input_types():
    """Fixed Win32 ABI layouts; constructing these performs no OS interaction."""
    import ctypes as c
    class Keyboard(c.Structure):
        _fields_ = [('vk', c.c_uint16), ('scan', c.c_uint16), ('flags', c.c_uint32),
                    ('time', c.c_uint32), ('extra', c.c_size_t)]
    class Mouse(c.Structure):
        _fields_ = [('x', c.c_int32), ('y', c.c_int32), ('data', c.c_uint32),
                    ('flags', c.c_uint32), ('time', c.c_uint32), ('extra', c.c_size_t)]
    class Hardware(c.Structure):
        _fields_ = [('message', c.c_uint32), ('low', c.c_uint16), ('high', c.c_uint16)]
    class Payload(c.Union):
        _fields_ = [('keyboard', Keyboard), ('mouse', Mouse), ('hardware', Hardware)]
    class Input(c.Structure):
        _fields_ = [('type', c.c_uint32), ('payload', Payload)]
    class Rect(c.Structure):
        _fields_ = [('left', c.c_int32), ('top', c.c_int32), ('right', c.c_int32), ('bottom', c.c_int32)]
    class Gui(c.Structure):
        _fields_ = [('size', c.c_uint32), ('flags', c.c_uint32)] + [
            (name, c.c_void_p) for name in ('active', 'focus', 'capture', 'menu', 'move', 'caret')] + [('rect', Rect)]
    return Input, Gui


def unicode_input(value, Input):
    require(type(value) is str and 0 < len(value) <= 1024 and not any(ord(c) < 32 for c in value),
            'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
    encoded = value.encode('utf-16-le')
    units = [int.from_bytes(encoded[i:i+2], 'little') for i in range(0, len(encoded), 2)]
    events = (Input * (len(units)*2))()
    for i, unit in enumerate(units):
        for index, flags in ((i*2, 4), (i*2+1, 6)):
            events[index].type = 1
            events[index].payload.keyboard.scan = unit
            events[index].payload.keyboard.flags = flags
    return events


def choose_controls(api, dialog, pid, path):
    require(api.owner(dialog) == pid and api.title(dialog) == TITLE
            and api.kind(dialog) == '#32770', 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    controls = api.children(dialog)
    edits = [h for h in controls if api.kind(h) == 'Edit' and api.control_id(h) == 1152]
    buttons = [h for h in controls if api.kind(h) == 'Button' and api.control_id(h) == 1]
    require(len(edits) == len(buttons) == 1, 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    edit, button = edits[0], buttons[0]
    require(all(api.owner(h) == pid and api.child_of(dialog, h) for h in (edit, button))
            and api.owner(dialog) == pid, 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    require(api.set_text(edit, path, dialog, pid) and api.text(edit) == path, 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
    # The input was set once. Any changed owner or control is a refusal, never
    # a reason to retype, click a different window, or retry a folder choice.
    require(api.owner(dialog) == pid and api.owner(button) == pid and api.child_of(dialog, button),
            'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    return api.click(button)


def read_original_choice(page, delivery_acknowledged, attempt):
    """Read the one existing native result even when Windows did not acknowledge."""
    require(type(delivery_acknowledged) is bool, 'DESKTOP_PAIR_PICKER_DELIVERY_INVALID')
    with (attempt / 'picker-click-delivery.json').open('xb') as output:
        output.write(canonical({'message_acknowledged': delivery_acknowledged,
            'delivery': 'acknowledged' if delivery_acknowledged else 'unknown',
            'original_click_sent_once': True, 'selection_inferred_from_acknowledgement': False}))
        output.flush(); os.fsync(output.fileno())
    try:
        page.wait_for_function('()=>window.ownedVaultChoiceResult!==null')
        return page.evaluate('''() => ({...window.ownedVaultChoiceResult,
            actualPath:pairPinia._s.get('workspace').vaultPath,
            vaultId:pairPinia._s.get('workspace').vaultId})''')
    except Exception:
        # The original promise may still be running. No new native choice,
        # click, input, or authorization is created to turn it into success.
        require(False, 'DESKTOP_PAIR_PICKER_OUTCOME_UNKNOWN')


class WindowsDialog:
    def __init__(self, context):
        from android_desktop_product import original_marker, owned_root
        original_marker(owned_root(os.environ, context), context)
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.api = ctypes.WinDLL('user32', use_last_error=True)
        self.api.FindWindowExW.argtypes = [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]
        self.api.FindWindowExW.restype = w.HWND
        self.api.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.api.GetWindowThreadProcessId.restype = w.DWORD
        self.api.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.api.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
        self.api.GetDlgCtrlID.argtypes = [w.HWND]
        self.api.GetDlgCtrlID.restype = ctypes.c_int
        self.api.IsChild.argtypes = [w.HWND, w.HWND]
        self.api.IsChild.restype = w.BOOL
        self.callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self.api.EnumChildWindows.argtypes = [w.HWND, self.callback, w.LPARAM]
        self.api.EnumChildWindows.restype = w.BOOL
        self.api.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
        self.api.SendMessageW.restype = w.LPARAM
        self.api.SendMessageTimeoutW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM,
            w.UINT, w.UINT, ctypes.POINTER(ctypes.c_size_t)]
        self.api.SendMessageTimeoutW.restype = w.LPARAM
        self.Input, self.Gui = native_input_types()
        self.api.GetForegroundWindow.restype = w.HWND
        self.api.SetForegroundWindow.argtypes = [w.HWND]
        self.api.SetForegroundWindow.restype = w.BOOL
        self.api.GetGUIThreadInfo.argtypes = [w.DWORD, ctypes.POINTER(self.Gui)]
        self.api.GetGUIThreadInfo.restype = w.BOOL
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.api.SendInput.argtypes = [w.UINT, ctypes.POINTER(self.Input), ctypes.c_int]
        self.api.SendInput.restype = w.UINT

    def owner(self, handle):
        value = self.w.DWORD()
        self.api.GetWindowThreadProcessId(handle, self.ctypes.byref(value))
        return value.value

    def title(self, handle):
        value = self.ctypes.create_unicode_buffer(4096)
        self.api.GetWindowTextW(handle, value, len(value))
        return value.value

    def kind(self, handle):
        value = self.ctypes.create_unicode_buffer(128)
        self.api.GetClassNameW(handle, value, len(value))
        return value.value

    def control_id(self, handle):
        return self.api.GetDlgCtrlID(handle)

    def child_of(self, parent, child):
        return bool(self.api.IsChild(parent, child))

    def children(self, handle):
        result = []
        @self.callback
        def collect(child, _):
            result.append(child)
            return len(result) < 256
        self.api.EnumChildWindows(handle, collect, 0)
        require(len(result) < 256, 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
        return result

    def dialogs(self, pid):
        result, previous = [], None
        while True:
            previous = self.api.FindWindowExW(None, previous, '#32770', TITLE)
            if not previous:
                break
            if self.owner(previous) == pid:
                result.append(previous)
            require(len(result) <= 1, 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
        return result

    def input_target(self, handle, dialog, pid):
        require(self.owner(dialog) == self.owner(handle) == pid and self.child_of(dialog, handle)
                and self.kind(handle) == 'Edit' and self.control_id(handle) == 1152
                and self.kind(dialog) == '#32770' and self.title(dialog) == TITLE,
                'DESKTOP_PAIR_PICKER_TARGET_INVALID')

    def input_focused(self, handle, dialog, pid):
        self.input_target(handle, dialog, pid)
        require(self.api.GetForegroundWindow() == dialog, 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
        info = self.Gui(); info.size = self.ctypes.sizeof(info)
        return bool(self.api.GetGUIThreadInfo(0, self.ctypes.byref(info)) and info.active == dialog and info.focus == handle)

    def focus_input(self, handle, dialog, pid):
        self.input_target(handle, dialog, pid)
        self.api.SetForegroundWindow(dialog)  # Exactly one original owned activation, never another window.
        require(self.api.GetForegroundWindow() == dialog, 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
        result = self.ctypes.c_size_t()
        require(self.api.SendMessageTimeoutW(dialog, 0x0028, handle, 1, 2, 1000, self.ctypes.byref(result)),
                'DESKTOP_PAIR_PICKER_INPUT_REFUSED')  # WM_NEXTDLGCTL focuses this exact Edit.
        until = time.monotonic() + 2
        while not self.input_focused(handle, dialog, pid):
            require(time.monotonic() < until, 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
            time.sleep(.05)  # Read the same focus result, never send another activation.

    def set_text(self, handle, value, dialog, pid):
        events = unicode_input(value, self.Input)
        self.input_result = {'method': 'owned-native-unicode-keyboard', 'expected_events': len(events),
            'inserted_events': 0, 'original_focus_verified': False, 'readback_matches': False, 'automatic_retry': False}
        self.focus_input(handle, dialog, pid)
        require(not any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (0x10,0x11,0x12,0x5B,0x5C)),
                'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
        self.input_result['original_focus_verified'] = True
        self.api.SendMessageW(handle, 0x00B1, 0, -1)  # Select the initial label once, no clipboard.
        require(self.input_focused(handle, dialog, pid), 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
        inserted = self.api.SendInput(len(events), events, self.ctypes.sizeof(self.Input))
        self.input_result['inserted_events'] = inserted
        if inserted != len(events): return False  # Unknown partial stream; no remaining-key retry or confirm.
        until = time.monotonic() + 3
        while True:
            require(self.input_focused(handle, dialog, pid), 'DESKTOP_PAIR_PICKER_INPUT_REFUSED')
            if self.text(handle) == value:
                self.input_result['readback_matches'] = True
                return True
            if time.monotonic() >= until: return False
            time.sleep(.05)

    def text(self, handle):
        value = self.ctypes.create_unicode_buffer(4096)
        self.api.SendMessageW(handle, 0x000D, len(value), self.ctypes.cast(value, self.ctypes.c_void_p).value)
        return value.value

    def click(self, handle):
        result = self.ctypes.c_size_t()
        # Timeout means unknown delivery, so the original click is never sent again.
        return bool(self.api.SendMessageTimeoutW(handle, 0x00F5, 0, 0, 2, 10000, self.ctypes.byref(result)))


def choose_owned_vault(page, context, process, vault, attempt):
    # Importing or testing this module never constructs user32. Revalidate the
    # original hosted run, root and process before any window enumeration.
    from android_desktop_product import directory_identity, original_marker, owned_root, windows_drive_path
    root = owned_root(os.environ, context)
    original_marker(root, context)
    require(vault == root / 'vault' and process is not None and process.poll() is None,
            'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    require(attempt == root / 'stage-1', 'DESKTOP_PAIR_PICKER_TARGET_INVALID')
    with (attempt / 'choose-vault-intent.json').open('xb') as output:
        output.write(canonical({'context': context, 'original_native_pid': process.pid,
            'owned_vault_identity': directory_identity(vault), 'native_folder_choice_not_yet_confirmed': True}))
        output.flush(); os.fsync(output.fileno())
    page.evaluate('''() => {
        window.ownedVaultChoiceResult = null;
        const workspace = pairPinia._s.get('workspace');
        window.ownedVaultChoice = workspace.openVault('').then(
            () => { window.ownedVaultChoiceResult = {ok:true}; },
            () => { window.ownedVaultChoiceResult = {ok:false}; });
    }''')
    api, until = WindowsDialog(context), time.monotonic() + 30
    dialogs = []
    while time.monotonic() < until:
        require(process.poll() is None, 'DESKTOP_PAIR_PICKER_UNAVAILABLE')
        dialogs = api.dialogs(process.pid)
        if dialogs:
            break
        time.sleep(.15)
    require(len(dialogs) == 1, 'DESKTOP_PAIR_PICKER_UNAVAILABLE')
    allowed = {'Edit', 'Button', 'ComboBox', 'ComboBoxEx32', 'DirectUIHWND', 'ToolbarWindow32', 'Static'}
    shape = [{'class': api.kind(h) if api.kind(h) in allowed else 'Other', 'control_id': api.control_id(h),
              'same_original_pid': api.owner(h) == process.pid, 'original_dialog_child': api.child_of(dialogs[0], h)}
             for h in api.children(dialogs[0])]
    with (attempt / 'picker-shape.json').open('xb') as output:
        output.write(canonical({'controls': shape, 'raw_labels_or_values_returned': False})); output.flush(); os.fsync(output.fileno())
    try:
        acknowledged = choose_controls(api, dialogs[0], process.pid, str(vault))
    finally:
        with (attempt / 'picker-input.json').open('xb') as output:
            output.write(canonical(getattr(api, 'input_result', {'input_not_started': True})))
            output.flush(); os.fsync(output.fileno())
    result = read_original_choice(page, acknowledged, attempt)
    require(type(result.get('ok')) is bool, 'DESKTOP_PAIR_PICKER_OUTCOME_UNKNOWN')
    matches = bool(result['ok'] and windows_drive_path(result['actualPath']) == windows_drive_path(vault))
    actual = windows_drive_path(result['actualPath']) if result['ok'] else None
    location = 'native-refused'
    if actual is not None:
        location = next((name for name, path in [('expected-vault', vault), ('owned-run-root', root), ('owned-data-root', root/'data')]
            if actual == windows_drive_path(path)),
            'other-owned-directory' if actual.startswith(windows_drive_path(root)+'\\') else 'outside-owned-root')
    with (attempt / 'picker-native-result.json').open('xb') as output:
        output.write(canonical({'original_choice_settled': True, 'native_ok': result['ok'],
            'selected_path_matches_owned_vault': matches, 'native_vault_id_present': bool(result.get('vaultId')),
            'selected_path_location': location, 'raw_path_or_error_returned': False}))
        output.flush(); os.fsync(output.fileno())
    require(result['ok'], 'DESKTOP_PAIR_PICKER_NATIVE_REFUSED')
    require(matches and bool(result.get('vaultId')), 'DESKTOP_PAIR_PICKER_TARGET_MISMATCH')
    return {'actual_native_folder_picker': True, 'native_vault_id': result['vaultId'],
            'original_owned_directory_selected': True, 'recent_grant_database_not_seeded': True,
            'only_original_process_controls_used': True, 'clipboard_or_other_windows_used': False}
