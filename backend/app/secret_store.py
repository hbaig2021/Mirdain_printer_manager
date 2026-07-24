from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Mapping


SERVICE_NAME = "mirdain-print-hub"
DATA_DIRECTORY = Path(__file__).resolve().parents[1] / "data"
SECRET_DIRECTORY = DATA_DIRECTORY / "secrets"
MASTER_KEY_PATH = DATA_DIRECTORY / ".secret-key"


class SecretStoreError(RuntimeError):
    pass


def _keyring():
    try:
        import keyring
    except ImportError as exc:
        raise SecretStoreError("The secure credential component is unavailable. Reinstall Mirdain Print Hub and try again.") from exc
    return keyring


def _parse(raw: str) -> dict[str, str]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise SecretStoreError("The saved printer credential could not be read.") from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise SecretStoreError("The saved printer credential has an invalid format.")
    return payload


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise SecretStoreError(
            "The encrypted credential component is unavailable. Reinstall Mirdain Print Hub and try again."
        ) from exc

    configured = os.getenv("MIRDAIN_SECRET_KEY", "").strip()
    if configured:
        try:
            key = configured.encode("ascii")
        except UnicodeEncodeError as exc:
            raise SecretStoreError(
                "MIRDAIN_SECRET_KEY is not a valid Fernet key."
            ) from exc
    else:
        DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
        try:
            key = MASTER_KEY_PATH.read_bytes().strip()
        except FileNotFoundError:
            generated = Fernet.generate_key()
            try:
                with MASTER_KEY_PATH.open("xb") as handle:
                    handle.write(generated)
                _restrict_permissions(MASTER_KEY_PATH)
                key = generated
            except FileExistsError:
                key = MASTER_KEY_PATH.read_bytes().strip()
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise SecretStoreError(
            "MIRDAIN_SECRET_KEY is not a valid Fernet key."
        ) from exc


def _secret_path(credential_id: str) -> Path:
    digest = hashlib.sha256(credential_id.encode("utf-8")).hexdigest()
    return SECRET_DIRECTORY / f"{digest}.secret"


def _restrict_permissions(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ACLs and some mounted volumes do not expose POSIX modes.
        pass


def _save_encrypted(credential_id: str, raw: str) -> None:
    SECRET_DIRECTORY.mkdir(parents=True, exist_ok=True)
    destination = _secret_path(credential_id)
    temporary = destination.with_suffix(".tmp")
    try:
        temporary.write_bytes(_fernet().encrypt(raw.encode("utf-8")))
        _restrict_permissions(temporary)
        os.replace(temporary, destination)
        _restrict_permissions(destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise SecretStoreError(
            "This system could not securely store the printer credential."
        ) from exc


def _load_encrypted(credential_id: str) -> str | None:
    path = _secret_path(credential_id)
    try:
        token = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecretStoreError(
            "This system could not access the saved printer credential."
        ) from exc
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except Exception as exc:
        raise SecretStoreError(
            "The saved printer credential could not be decrypted. Check MIRDAIN_SECRET_KEY."
        ) from exc


def save(credential_id: str, values: Mapping[str, str]) -> None:
    cleaned: dict[str, str] = {}
    for key, value in values.items():
        if key == "password":
            # Password whitespace can be intentional. API keys and usernames,
            # on the other hand, are copied from form fields and normalized.
            if value:
                cleaned[key] = value
            continue
        normalized = value.strip()
        if normalized:
            cleaned[key] = normalized
    if not cleaned:
        return
    raw = json.dumps(cleaned)
    try:
        _keyring().set_password(SERVICE_NAME, credential_id, raw)
        _secret_path(credential_id).unlink(missing_ok=True)
        return
    except Exception:
        # Headless Linux and containers commonly have no system keyring.
        _save_encrypted(credential_id, raw)


def load(credential_id: str | None) -> dict[str, str]:
    if not credential_id:
        return {}
    try:
        raw = _keyring().get_password(SERVICE_NAME, credential_id)
    except Exception:
        raw = None
    if not raw:
        raw = _load_encrypted(credential_id)
    if not raw:
        return {}
    return _parse(raw)


def remove(credential_id: str | None) -> None:
    if not credential_id:
        return
    try:
        _keyring().delete_password(SERVICE_NAME, credential_id)
    except Exception:
        # A missing OS-keyring value is already the desired end state.
        pass
    _secret_path(credential_id).unlink(missing_ok=True)
