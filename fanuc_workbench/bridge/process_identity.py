"""Creation identity for already-owned Windows handles; never enumerate processes."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os


def creation_time(handle, *, kernel=None):
    if kernel is None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.GetProcessTimes.restype = wintypes.BOOL
    values = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
        raise OSError("Owned process creation time is unavailable")
    return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime


def current_creation_time():
    if os.name != "nt":
        return None
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    return creation_time(kernel.GetCurrentProcess())
