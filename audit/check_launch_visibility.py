"""Exercise real packaged startup and repeat activation with isolated user data."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

user32 = ctypes.WinDLL('user32', use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
root = Path(__file__).resolve().parents[1]

def windows():
    found = []
    @callback_type
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value == 'ArcScaler':
            found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    return found

def wait_for(predicate):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Timed out waiting for packaged window state')

def main():
    existing = set(windows())
    hwnd = None
    with tempfile.TemporaryDirectory(prefix='arcscaler-visible-') as temp:
        env = dict(os.environ, ARCSCALER_DATA_DIR=temp)
        command = [str(root / 'dist/ArcScaler.exe')]
        process = subprocess.Popen(command, env=env, cwd=root)
        try:
            hwnd = wait_for(lambda: next((h for h in windows() if h not in existing and user32.IsWindowVisible(h)), None))
            time.sleep(2)
            assert user32.IsWindowVisible(hwnd), 'Window disappeared after startup'
            user32.ShowWindow(hwnd, 0)
            assert not user32.IsWindowVisible(hwnd)
            second = subprocess.run(command, env=env, cwd=root, timeout=40)
            assert second.returncode == 0, 'Repeat launch failed'
            wait_for(lambda: user32.IsWindowVisible(hwnd))
            report = {'startup_visible': True, 'hidden_window_restored_by_repeat_launch': True}
            (root / 'audit/launch-visibility-result.json').write_text(json.dumps(report, indent=2))
            print(json.dumps(report))
        finally:
            if hwnd:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)
            process.wait(timeout=40)

if __name__ == '__main__':
    main()
