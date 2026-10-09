"""Store connector credentials outside ordinary connection settings.

SQLite holds only a random reference. Legacy JSON rows remain readable until
an explicit, offline migration replaces them. A locked or unavailable Keychain
is an error, never a reason to fall back to a plaintext copy.
"""
from __future__ import annotations

import json
import ctypes
import sys
import uuid
from functools import lru_cache
from typing import Any

from .config import APP


SERVICE = "com.vortexveri.portal.connections"
REFERENCE_KEY = "$keychain_ref"
FERNET_KEY = "$fernet"
# Current provider secret fields plus OAuth refresh tokens. Defend the shared
# storage boundary even if an older caller put one of these in config.
SECRET_FIELD_NAMES = frozenset({
    "access_token", "api_key", "client_secret", "developer_token",
    "refresh_token", "token",
})


class SecretStoreError(RuntimeError):
    """A credential could not be securely stored, read, or removed."""


def _fernet():
    """Load the server-only encryption backend when explicitly configured."""
    if APP.secret_store_backend != "fernet":
        return None
    if not APP.secret_encryption_key:
        raise SecretStoreError("PORTAL_SECRET_ENCRYPTION_KEY is required for the Fernet secret store")
    try:
        from cryptography.fernet import Fernet, InvalidToken
        return Fernet(APP.secret_encryption_key.encode("ascii")), InvalidToken
    except ImportError as exc:
        raise SecretStoreError("Install cryptography for the Fernet secret store") from exc
    except (ValueError, TypeError) as exc:
        raise SecretStoreError("PORTAL_SECRET_ENCRYPTION_KEY is invalid") from exc


def _encrypt(values: dict[str, Any]) -> str:
    backend = _fernet()
    if backend is None:
        raise SecretStoreError("Encrypted server secrets are not configured")
    fernet, _ = backend
    raw = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return fernet.encrypt(raw).decode("ascii")


def _decrypt(token: str) -> dict[str, Any]:
    backend = _fernet()
    if backend is None:
        raise SecretStoreError("Encrypted server secrets are not configured")
    fernet, invalid_token = backend
    try:
        value = json.loads(fernet.decrypt(token.encode("ascii")).decode("utf-8"))
    except (invalid_token, UnicodeError, ValueError) as exc:
        raise SecretStoreError("Encrypted connection credential is unavailable") from exc
    if not isinstance(value, dict):
        raise SecretStoreError("Invalid encrypted connection credential")
    return value


@lru_cache(maxsize=1)
def _frameworks():
    if sys.platform != "darwin":
        raise SecretStoreError("Connector credentials require macOS Keychain")
    try:
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    except OSError as exc:
        raise SecretStoreError("macOS Keychain is unavailable") from exc
    sec.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    sec.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    sec.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    sec.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    sec.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    sec.SecKeychainItemFreeContent.restype = ctypes.c_int32
    sec.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    sec.SecKeychainItemDelete.restype = ctypes.c_int32
    core.CFRelease.argtypes = [ctypes.c_void_p]
    return sec, core


def _key(reference: str) -> tuple[bytes, bytes]:
    if not reference:
        raise SecretStoreError("Invalid Keychain credential reference")
    return SERVICE.encode("utf-8"), reference.encode("utf-8")


def _find_item(reference: str):
    sec, core = _frameworks()
    service, account = _key(reference)
    item = ctypes.c_void_p()
    status = sec.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account, None, None, ctypes.byref(item))
    if status != 0:
        raise SecretStoreError("Keychain credential is unavailable; unlock Keychain and retry")
    return sec, core, item


def new_reference() -> str:
    return uuid.uuid4().hex


def marker(reference: str) -> str:
    return json.dumps({REFERENCE_KEY: reference})


def reference(raw: str | None) -> str | None:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise SecretStoreError("Invalid connection credential record") from exc
    if not isinstance(value, dict):
        raise SecretStoreError("Invalid connection credential record")
    if FERNET_KEY in value:
        if len(value) != 1 or not isinstance(value[FERNET_KEY], str) or not value[FERNET_KEY]:
            raise SecretStoreError("Invalid encrypted connection credential")
        return None
    if REFERENCE_KEY not in value:
        return None
    ref = value[REFERENCE_KEY]
    if len(value) != 1 or not isinstance(ref, str) or not ref:
        raise SecretStoreError("Invalid Keychain credential reference")
    return ref


def put(reference: str, values: dict[str, Any]) -> None:
    if not reference or not isinstance(values, dict):
        raise SecretStoreError("Invalid Keychain credential")
    # The security CLI silently truncates long values supplied through its
    # interactive -w prompt. Security.framework accepts the full byte buffer.
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    sec, core = _frameworks()
    service, account = _key(reference)
    item = ctypes.c_void_p()
    status = sec.SecKeychainAddGenericPassword(
        None, len(service), service, len(account), account,
        len(payload), ctypes.c_char_p(payload), ctypes.byref(item))
    if item.value:
        core.CFRelease(item)
    if status == -25299:  # errSecDuplicateItem; never overwrite a referenced secret.
        raise SecretStoreError("Keychain credential reference already exists")
    if status != 0:
        raise SecretStoreError("Could not save Keychain credential; unlock Keychain and retry")


def get(reference: str) -> dict[str, Any]:
    sec, core = _frameworks()
    service, account = _key(reference)
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = sec.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account,
        ctypes.byref(length), ctypes.byref(data), ctypes.byref(item))
    if status != 0:
        raise SecretStoreError("Keychain credential is unavailable; unlock Keychain and retry")
    try:
        raw = ctypes.string_at(data, length.value)
    finally:
        sec.SecKeychainItemFreeContent(None, data)
        if item.value:
            core.CFRelease(item)
    try:
        values = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise SecretStoreError("Invalid Keychain credential content") from exc
    if not isinstance(values, dict):
        raise SecretStoreError("Invalid Keychain credential content")
    return values


def delete(reference: str) -> None:
    sec, core, item = _find_item(reference)
    try:
        status = sec.SecKeychainItemDelete(item)
    finally:
        core.CFRelease(item)
    if status != 0:
        raise SecretStoreError("Could not remove Keychain credential")


def resolve(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise SecretStoreError("Invalid connection credential record") from exc
    if isinstance(value, dict) and FERNET_KEY in value:
        if len(value) != 1 or not isinstance(value[FERNET_KEY], str):
            raise SecretStoreError("Invalid encrypted connection credential")
        return _decrypt(value[FERNET_KEY])
    ref = reference(raw)
    if ref is not None:
        return get(ref)
    # Compatibility for existing installations until explicit migration. New
    # connection writes never use this representation.
    return json.loads(raw or "{}")


def stage(values: dict[str, Any]) -> tuple[str, str | None]:
    """Write a new item before committing its reference to SQLite."""
    if not values:
        return "{}", None
    if APP.secret_store_backend == "fernet":
        return json.dumps({FERNET_KEY: _encrypt(values)}, separators=(",", ":")), None
    if APP.secret_store_backend != "keychain":
        raise SecretStoreError("PORTAL_SECRET_STORE must be keychain or fernet")
    ref = new_reference()
    put(ref, values)
    return marker(ref), ref


def promote_config(config: dict[str, Any], secrets: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Move recognized credentials out of public connection settings."""
    public = dict(config)
    private = dict(secrets)
    for key in SECRET_FIELD_NAMES & public.keys():
        if key not in private or not private[key]:
            private[key] = public[key]
        del public[key]
    return public, private
