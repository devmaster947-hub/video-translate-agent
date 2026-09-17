"""Secure provider credentials: a private macOS file or the system keyring."""
from getpass import getpass
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

import keyring
from keyring.errors import KeyringError


SERVICE_NAME = "video-translate-agent"
LEGACY_SERVICE_NAME = "video-translate-skill"
PROVIDERS = ("minimax", "elevenlabs")
STORED_PROVIDERS = (*PROVIDERS, "lingzhi")
CREDENTIAL_PROVIDERS = (*STORED_PROVIDERS, "lzstudio")
MAX_CREDENTIAL_CHARS = 4096


class CredentialError(RuntimeError):
    """Stable credential error that never includes secret material."""


def _uses_local_user_file() -> bool:
    return sys.platform == "darwin"


def _local_credential_file() -> Path:
    return Path.home() / "Library" / "Application Support" / SERVICE_NAME / "credentials.json"


def credential_storage_name() -> str:
    return "local-user-file" if _uses_local_user_file() else "system-keyring"


def _read_local_credentials(*, strict: bool) -> dict[str, str]:
    path = _local_credential_file()
    try:
        if not path.exists():
            return {}
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise CredentialError("LOCAL_CREDENTIAL_FILE_UNSAFE")
        os.chmod(path, 0o600)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError
        return {name: value.strip() for name, value in data.items()
                if name in STORED_PROVIDERS and isinstance(value, str) and value.strip()}
    except CredentialError:
        if strict:
            raise
    except (OSError, ValueError, json.JSONDecodeError):
        if strict:
            raise CredentialError("LOCAL_CREDENTIAL_FILE_UNAVAILABLE") from None
    return {}


def _write_local_credentials(values: dict[str, str]) -> None:
    path = _local_credential_file()
    directory = path.parent
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.is_symlink() or not stat.S_ISDIR(directory.stat().st_mode):
            raise CredentialError("LOCAL_CREDENTIAL_FILE_UNSAFE")
        os.chmod(directory, 0o700)
        if path.exists() and path.is_symlink():
            raise CredentialError("LOCAL_CREDENTIAL_FILE_UNSAFE")
        descriptor, temporary = tempfile.mkstemp(prefix=".credentials-", dir=directory)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(values, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except CredentialError:
        raise
    except OSError:
        raise CredentialError("LOCAL_CREDENTIAL_FILE_UNAVAILABLE") from None
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def _provider(provider: str) -> str:
    value = provider.strip().lower()
    if value == "lzstudio":
        value = "lingzhi"
    if value not in STORED_PROVIDERS:
        raise CredentialError("UNSUPPORTED_CREDENTIAL_PROVIDER")
    return value


def read_credential(provider: str, *, strict: bool = False) -> str | None:
    """Read a credential without exposing backend details in errors."""
    name = _provider(provider)
    if _uses_local_user_file():
        return _read_local_credentials(strict=strict).get(name)
    try:
        value = keyring.get_password(SERVICE_NAME, name)
        if not value:
            value = keyring.get_password(LEGACY_SERVICE_NAME, name)
            if value:
                try:
                    keyring.set_password(SERVICE_NAME, name, value)
                except (KeyringError, RuntimeError, OSError):
                    pass
    except (KeyringError, RuntimeError, OSError):
        if strict:
            raise CredentialError("SYSTEM_KEYRING_UNAVAILABLE") from None
        return None
    return value.strip() if value and value.strip() else None


def store_credential(provider: str) -> None:
    """Prompt without echo and persist a credential in the system keyring."""
    name = _provider(provider)
    value = getpass(f"Enter {name} API key (input hidden): ").strip()
    store_credential_value(name, value)


def store_credential_value(provider: str, value: str) -> None:
    """Persist an already-captured credential without exposing it."""
    name = _provider(provider)
    value = value.strip()
    if not value:
        raise CredentialError("EMPTY_CREDENTIAL")
    if _uses_local_user_file():
        values = _read_local_credentials(strict=True)
        values[name] = value
        _write_local_credentials(values)
        return
    try:
        keyring.set_password(SERVICE_NAME, name, value)
    except (KeyringError, RuntimeError, OSError):
        raise CredentialError("SYSTEM_KEYRING_UNAVAILABLE") from None


def store_credential_from_stream(provider: str, stream=None) -> None:
    """Read one bounded line from stdin and persist it without echoing it."""
    source = stream if stream is not None else sys.stdin
    value = source.readline(MAX_CREDENTIAL_CHARS + 2)
    content = value[:-1] if value.endswith("\n") else value
    if len(content) > MAX_CREDENTIAL_CHARS:
        raise CredentialError("CREDENTIAL_INPUT_TOO_LARGE")
    store_credential_value(provider, value)


def delete_credential(provider: str) -> bool:
    """Delete a stored credential, returning False when none exists."""
    name = _provider(provider)
    if _uses_local_user_file():
        values = _read_local_credentials(strict=True)
        if name not in values:
            return False
        del values[name]
        if values:
            _write_local_credentials(values)
        else:
            try:
                _local_credential_file().unlink(missing_ok=True)
            except OSError:
                raise CredentialError("LOCAL_CREDENTIAL_FILE_UNAVAILABLE") from None
        return True
    if read_credential(name, strict=True) is None:
        return False
    try:
        for service in (SERVICE_NAME, LEGACY_SERVICE_NAME):
            if keyring.get_password(service, name) is not None:
                keyring.delete_password(service, name)
    except (KeyringError, RuntimeError, OSError):
        raise CredentialError("SYSTEM_KEYRING_UNAVAILABLE") from None
    return True
