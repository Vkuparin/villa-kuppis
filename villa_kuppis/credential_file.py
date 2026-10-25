"""MELCloud credentials from a JSON secret file (Docker secret or 0600 file)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Credentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


class CredentialError(RuntimeError):
    pass


def read_credentials_file(path) -> Credentials:
    """Read {"username": ..., "password": ...}; errors never include file contents."""
    path = Path(path)
    if not path.is_absolute():
        raise CredentialError("Credential file path must be absolute")
    try:
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise CredentialError(
                "Credential file must not be group or world accessible (chmod 600)")
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except CredentialError:
        raise
    except OSError:
        raise CredentialError("Credential file cannot be read") from None
    except ValueError:
        raise CredentialError("Credential file is not valid JSON") from None
    if not isinstance(raw, dict) or set(raw) != {"username", "password"}:
        raise CredentialError('Credential file must hold only "username" and "password"')
    username, password = raw["username"], raw["password"]
    if (not isinstance(username, str) or not isinstance(password, str)
            or not username.strip() or not password):
        raise CredentialError("Credential file username and password must be non-empty text")
    return Credentials(username, password)


def read_token_file(path) -> str:
    """Read a plain-text access token; errors never include file contents."""
    path = Path(path)
    if not path.is_absolute():
        raise CredentialError("Token file path must be absolute")
    try:
        if os.name != "nt" and path.stat().st_mode & 0o077:
            raise CredentialError(
                "Token file must not be group or world accessible (chmod 600)")
        token = path.read_text(encoding="utf-8-sig").strip()
    except CredentialError:
        raise
    except (OSError, UnicodeDecodeError):
        raise CredentialError("Token file cannot be read") from None
    if not token or any(c.isspace() for c in token):
        raise CredentialError("Token file must hold one token on a single line")
    return token
