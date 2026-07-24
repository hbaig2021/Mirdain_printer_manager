from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import secret_store


class _UnavailableKeyring:
    def set_password(self, *_: object) -> None:
        raise RuntimeError("no keyring")

    def get_password(self, *_: object) -> str | None:
        raise RuntimeError("no keyring")

    def delete_password(self, *_: object) -> None:
        raise RuntimeError("no keyring")


class SecretStoreTests(unittest.TestCase):
    def test_encrypted_file_fallback_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            with (
                patch.object(secret_store, "DATA_DIRECTORY", data),
                patch.object(secret_store, "SECRET_DIRECTORY", data / "secrets"),
                patch.object(secret_store, "MASTER_KEY_PATH", data / ".secret-key"),
                patch.object(
                    secret_store,
                    "_keyring",
                    return_value=_UnavailableKeyring(),
                ),
                patch.dict(os.environ, {"MIRDAIN_SECRET_KEY": ""}),
            ):
                secret_store.save("printer:test", {"api_key": "very-secret"})
                stored_path = secret_store._secret_path("printer:test")

                self.assertTrue(stored_path.is_file())
                self.assertTrue(secret_store.MASTER_KEY_PATH.is_file())
                self.assertNotIn(b"very-secret", stored_path.read_bytes())
                self.assertEqual(
                    secret_store.load("printer:test"),
                    {"api_key": "very-secret"},
                )

                secret_store.remove("printer:test")
                self.assertFalse(stored_path.exists())

    def test_password_whitespace_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            with (
                patch.object(secret_store, "DATA_DIRECTORY", data),
                patch.object(secret_store, "SECRET_DIRECTORY", data / "secrets"),
                patch.object(secret_store, "MASTER_KEY_PATH", data / ".secret-key"),
                patch.object(
                    secret_store,
                    "_keyring",
                    return_value=_UnavailableKeyring(),
                ),
                patch.dict(os.environ, {"MIRDAIN_SECRET_KEY": ""}),
            ):
                secret_store.save(
                    "printer:test",
                    {
                        "username": " maker ",
                        "password": " leading and trailing ",
                    },
                )

                self.assertEqual(
                    secret_store.load("printer:test"),
                    {
                        "username": "maker",
                        "password": " leading and trailing ",
                    },
                )

    def test_invalid_environment_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data = Path(temporary_directory)
            with (
                patch.object(secret_store, "DATA_DIRECTORY", data),
                patch.object(secret_store, "SECRET_DIRECTORY", data / "secrets"),
                patch.object(secret_store, "MASTER_KEY_PATH", data / ".secret-key"),
                patch.object(
                    secret_store,
                    "_keyring",
                    return_value=_UnavailableKeyring(),
                ),
                patch.dict(
                    os.environ,
                    {"MIRDAIN_SECRET_KEY": "not-a-fernet-key"},
                ),
            ):
                with self.assertRaisesRegex(
                    secret_store.SecretStoreError,
                    "not a valid Fernet key",
                ):
                    secret_store.save(
                        "printer:test",
                        {"api_key": "very-secret"},
                    )

                self.assertFalse(secret_store._secret_path("printer:test").exists())


if __name__ == "__main__":
    unittest.main()
