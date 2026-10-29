"""Tokens live in Windows Credential Manager (via keyring), never in the database or a file."""

from __future__ import annotations

SERVICE = "meeting-assistant"


def get_secret(name: str) -> str | None:
    import keyring

    return keyring.get_password(SERVICE, name)


def set_secret(name: str, value: str) -> None:
    import keyring

    keyring.set_password(SERVICE, name, value)


def delete_secret(name: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass
