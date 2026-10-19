"""The current Windows user's MELCloud credential, outside project files."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os

DEFAULT_TARGET = "VillaKuppis/MELCloudHome"


@dataclass(frozen=True)
class Credentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


class CredentialError(RuntimeError):
    pass


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.c_void_p),
        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if os.name != "nt":
        raise CredentialError("Windows Credential Manager requires Windows")
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.POINTER(ctypes.POINTER(_Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def _check_target(target):
    if not isinstance(target, str) or not target.startswith("VillaKuppis/") or "\0" in target:
        raise CredentialError("Invalid Villa Kuppis credential target")


def read_credentials(target=DEFAULT_TARGET) -> Credentials:
    _check_target(target)
    api = _api()
    pointer = ctypes.POINTER(_Credential)()
    if not api.CredReadW(target, 1, 0, ctypes.byref(pointer)):
        raise CredentialError(f"Credential Manager read failed (Windows error {ctypes.get_last_error()}); enroll credentials under the task's Windows account")
    try:
        value = pointer.contents
        password = ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-16-le")
        if not value.UserName or not password:
            raise CredentialError("Stored MELCloud credential is empty")
        return Credentials(value.UserName, password)
    except UnicodeError:
        raise CredentialError("Stored MELCloud credential has invalid encoding") from None
    finally:
        api.CredFree(pointer)


def write_credentials(credentials: Credentials, target=DEFAULT_TARGET):
    _check_target(target)
    if not credentials.username.strip() or not credentials.password or "\0" in credentials.username:
        raise CredentialError("Email and password are required")
    encoded = credentials.password.encode("utf-16-le")
    if len(encoded) > 2560:
        raise CredentialError("Password exceeds the Windows credential size limit")
    blob = ctypes.create_string_buffer(encoded)
    value = _Credential()
    value.Type, value.Persist = 1, 2  # generic; same user, across logon sessions
    value.TargetName = target
    value.UserName = credentials.username.strip()
    value.CredentialBlobSize = len(encoded)
    value.CredentialBlob = ctypes.cast(blob, ctypes.c_void_p)
    if not _api().CredWriteW(ctypes.byref(value), 0):
        raise CredentialError(f"Credential Manager write failed (Windows error {ctypes.get_last_error()})")
