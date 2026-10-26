"""Create a private reporting directory and its retained handle in one call.

NtCreateFile FILE_OPEN_IF applies the descriptor only to a new object and returns
that object's handle. Existing objects are opened without ACL repair. The name
is one component relative to an already retained, non-reparse parent handle.
https://learn.microsoft.com/en-us/windows/win32/api/winternl/nf-winternl-ntcreatefile
"""


def open_private_directory(parent, name):
    import ctypes as c
    from ctypes import wintypes as w
    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32security

    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
        prefix + digit for prefix in ("COM", "LPT") for digit in "0123456789¹²³"}
    if (not isinstance(name, str) or not name or name.endswith((".", " "))
            or any(ord(ch) < 32 or ch in '<>:"/\\|?*' for ch in name)
            or name.split(".", 1)[0].upper() in reserved):
        raise ValueError("Reporting directory name must be one ordinary component")
    raw_name = name.encode("utf-16le")
    if len(raw_name) > 65532:
        raise ValueError("Reporting directory name is too long")
    name_buffer = c.create_string_buffer(raw_name + b"\0\0")
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    finally:
        token.Close()
    acl = win32security.ACL()
    for sid in (user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
        acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS,
            win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE, ntsecuritycon.FILE_ALL_ACCESS, sid)
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(user, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(win32security.SE_DACL_PROTECTED, win32security.SE_DACL_PROTECTED)
    if not descriptor.IsSelfRelative():
        raise ValueError("Reporting security descriptor must be self-relative")
    security_buffer = c.create_string_buffer(bytes(descriptor))

    class UnicodeString(c.Structure):
        _fields_ = [("length", w.USHORT), ("maximum", w.USHORT), ("buffer", c.c_void_p)]

    class ObjectAttributes(c.Structure):
        _fields_ = [("length", w.ULONG), ("root", w.HANDLE), ("name", c.POINTER(UnicodeString)),
                    ("attributes", w.ULONG), ("security", c.c_void_p), ("quality", c.c_void_p)]

    class IoStatus(c.Structure):
        _fields_ = [("status_or_pointer", c.c_void_p), ("information", c.c_size_t)]

    native = c.WinDLL("ntdll", use_last_error=True)
    native.NtCreateFile.argtypes = [c.POINTER(w.HANDLE), w.ULONG, c.POINTER(ObjectAttributes),
        c.POINTER(IoStatus), c.c_void_p, w.ULONG, w.ULONG, w.ULONG, w.ULONG, c.c_void_p, w.ULONG]
    native.NtCreateFile.restype = w.LONG
    native.RtlNtStatusToDosError.argtypes = [w.LONG]
    native.RtlNtStatusToDosError.restype = w.ULONG
    counted_name = UnicodeString(len(raw_name), len(raw_name) + 2, c.addressof(name_buffer))
    attributes = ObjectAttributes(c.sizeof(ObjectAttributes), int(parent), c.pointer(counted_name),
                                  0x40, c.addressof(security_buffer), None)  # OBJ_CASE_INSENSITIVE; noninheritable handle.
    handle, result = w.HANDLE(), IoStatus()
    # LIST_DIRECTORY is a read access that participates in share checking;
    # READ_ATTRIBUTES alone does not establish the required rename fence.
    status = native.NtCreateFile(c.byref(handle), win32con.READ_CONTROL | win32con.SYNCHRONIZE |
        ntsecuritycon.FILE_READ_ATTRIBUTES | ntsecuritycon.FILE_LIST_DIRECTORY,
        c.byref(attributes), c.byref(result), None,
        win32con.FILE_ATTRIBUTE_DIRECTORY, win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE,
        3, 0x00200021, None, 0)  # OPEN_IF; DIRECTORY | OPEN_REPARSE_POINT | SYNCHRONOUS_IO_NONALERT.
    if status != 0 or result.information not in {1, 2} or not handle.value:
        if handle.value:
            win32api.CloseHandle(handle.value)
        if status < 0:
            raise c.WinError(native.RtlNtStatusToDosError(status))
        raise OSError("Unexpected reporting directory create/open completion")
    try:
        return pywintypes.HANDLE(handle.value), result.information == 2
    except BaseException:
        win32api.CloseHandle(handle.value)
        raise
