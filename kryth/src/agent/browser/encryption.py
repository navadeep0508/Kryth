"""Optional Fernet encryption for browser profile data.

Falls back gracefully when the ``cryptography`` package is not installed —
all calls become no-ops that pass data through unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FERNET_AVAILABLE = False
try:
    from cryptography.fernet import Fernet, InvalidToken  # type: ignore[import]
    _FERNET_AVAILABLE = True
except ImportError:
    pass


def _derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key from a password + salt via PBKDF2."""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations=200_000)
    return base64.urlsafe_b64encode(dk)


class ProfileEncryption:
    """Encrypt and decrypt profile data blobs.

    Parameters
    ----------
    password:
        Human-chosen password.  If *None*, encryption is disabled.
    salt_file:
        Path where the per-profile salt is persisted.  Created on first use.
    """

    def __init__(self, password: str | None = None, salt_file: Path | None = None) -> None:
        self._enabled = bool(password and _FERNET_AVAILABLE)
        self._fernet = None

        if self._enabled:
            assert password is not None
            salt_file = salt_file or Path(os.devnull)
            salt = self._load_or_create_salt(salt_file)
            key = _derive_key(password, salt)
            from cryptography.fernet import Fernet  # type: ignore[import]
            self._fernet = Fernet(key)

        if password and not _FERNET_AVAILABLE:
            logger.warning(
                "ProfileEncryption: 'cryptography' not installed — "
                "profile data will NOT be encrypted. "
                "Run: pip install cryptography"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def encrypt_bytes(self, data: bytes) -> bytes:
        if not self._enabled or self._fernet is None:
            return data
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, data: bytes) -> bytes:
        if not self._enabled or self._fernet is None:
            return data
        try:
            from cryptography.fernet import InvalidToken  # type: ignore[import]
            return self._fernet.decrypt(data)
        except InvalidToken:
            raise ValueError("Decryption failed — wrong password or corrupted data.")

    def encrypt_json(self, obj: Any) -> bytes:
        return self.encrypt_bytes(json.dumps(obj).encode())

    def decrypt_json(self, data: bytes) -> Any:
        return json.loads(self.decrypt_bytes(data).decode())

    def encrypt_file(self, path: Path) -> None:
        """Encrypt *path* in-place (overwrites with ciphertext)."""
        if not self._enabled:
            return
        path.write_bytes(self.encrypt_bytes(path.read_bytes()))

    def decrypt_file(self, path: Path) -> None:
        """Decrypt *path* in-place."""
        if not self._enabled:
            return
        path.write_bytes(self.decrypt_bytes(path.read_bytes()))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_or_create_salt(salt_file: Path) -> bytes:
        if salt_file.exists():
            return salt_file.read_bytes()
        salt = os.urandom(16)
        salt_file.parent.mkdir(parents=True, exist_ok=True)
        salt_file.write_bytes(salt)
        return salt


# Convenience no-op instance used when no password is configured
NOOP_ENCRYPTION = ProfileEncryption(password=None)
