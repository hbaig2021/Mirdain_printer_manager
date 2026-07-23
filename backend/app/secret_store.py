from __future__ import annotations

import json
from typing import Mapping


SERVICE_NAME = "mirdain-print-hub"


class SecretStoreError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError("The secure credential component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    return keyring


def save(credential_id: str, values: Mapping[str, str]) -> None:
    cleaned = {key: value.strip() for key, value in values.items() if value and value.strip()}
    if not cleaned:
        return
    try:
        _keyring().set_password(SERVICE_NAME, credential_id, json.dumps(cleaned))
    except Exception as exc:  # Keyring backends expose platform-specific exceptions.
        raise SecretStoreError("Windows could not securely store this printer credential.") from exc


def load(credential_id: str | None) -> dict[str, str]:
    if not credential_id:
        return {}
    try:
        raw = _keyring().get_password(SERVICE_NAME, credential_id)
    except Exception as exc:
        raise SecretStoreError("Windows could not access this printer credential.") from exc
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SecretStoreError("The saved printer credential could not be read.") from exc
    if not isinstance(payload, dict) or not all(isinstance(value, str) for value in payload.values()):
        raise SecretStoreError("The saved printer credential has an invalid format.")
    return payload


def remove(credential_id: str | None) -> None:
    if not credential_id:
        return
    try:
        _keyring().delete_password(SERVICE_NAME, credential_id)
    except Exception:
        # A missing OS-keyring value is already the desired end state.
        return
