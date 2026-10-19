"""Read-only runtime API and Windows version-resource inspection; no DLL execution."""
from core.files import validate_pe
from core.pe import inspect_pe

REQUIRED_EXPORTS = {
    'libxess.dll': {'xessD3D12CreateContext', 'xessD3D12Init', 'xessD3D12Execute',
                    'xessDestroyContext', 'xessGetVersion'},
    'libxess_fg.dll': {'xefgSwapChainD3D12CreateContext', 'xefgSwapChainD3D12InitFromSwapChain',
                       'xefgSwapChainSetEnabled', 'xefgSwapChainDestroy'},
    'libxell.dll': {'xellD3D12CreateContext', 'xellAddMarkerData', 'xellSleep', 'xellDestroyContext'},
}


def file_version(path):
    try:
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL('version', use_last_error=True)
        api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        size = api.GetFileVersionInfoSizeW(str(path), None)
        if not size or size > 4 * 1024 * 1024: return None
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(str(path), 0, size, buffer): return None
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if not api.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52: return None
        info = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
        if info[0] != 0xFEEF04BD: return None
        return '.'.join(str(v) for v in (info[2] >> 16, info[2] & 65535, info[3] >> 16, info[3] & 65535))
    except (AttributeError, OSError):
        return None


def inspect_runtime(path, name):
    validate_pe(path)
    missing = REQUIRED_EXPORTS[name] - set(inspect_pe(path)['exports'])
    if missing:
        raise ValueError(f'{name} lacks required API exports: {", ".join(sorted(missing))}. Native file was not changed.')
    return {'version': file_version(path) or 'Unknown', 'api': 'Required exports present',
            'compatibility': 'Verify activation in game'}
