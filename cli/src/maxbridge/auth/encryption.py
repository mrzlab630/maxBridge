"""Session token encryption using Fernet (AES-128-CBC + HMAC-SHA256).

Key is derived from a master key file via PBKDF2. The key file is auto-generated
on first use and stored with 0o600 permissions.
"""

import base64
import logging
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("maxbridge.auth.encryption")

_SALT_LENGTH = 16
_KDF_ITERATIONS = 480_000


class TokenEncryptor:
    """Encrypt/decrypt session tokens using a machine-local master key."""

    def __init__(self, key_path: str) -> None:
        self._key_path = Path(key_path)
        self._master_key: bytes | None = None

    def ensure_key(self) -> None:
        """Load or generate the master key file."""
        if self._master_key is not None:
            return

        if self._key_path.exists():
            self._master_key = self._key_path.read_bytes()
            logger.info("Master key loaded")
        else:
            self._master_key = secrets.token_bytes(32)
            self._key_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._key_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            try:
                os.write(fd, self._master_key)
            finally:
                os.close(fd)
            logger.info("Master key generated and saved")

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext string. Returns base64-encoded salt:ciphertext."""
        self.ensure_key()
        salt = os.urandom(_SALT_LENGTH)
        fernet = self._derive_fernet(salt)
        ciphertext = fernet.encrypt(plaintext.encode("utf-8"))
        # Store as: base64(salt) + ":" + base64(ciphertext)
        return base64.urlsafe_b64encode(salt).decode() + ":" + ciphertext.decode()

    def decrypt(self, encrypted: str) -> str:
        """Decrypt a salt:ciphertext string back to plaintext.

        Raises ValueError if decryption fails (wrong key, corrupted data).
        """
        self.ensure_key()
        try:
            salt_b64, ciphertext = encrypted.split(":", 1)
            salt = base64.urlsafe_b64decode(salt_b64)
            fernet = self._derive_fernet(salt)
            return fernet.decrypt(ciphertext.encode()).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError) as e:
            raise ValueError(f"Decryption failed: {e}") from e

    def _derive_fernet(self, salt: bytes) -> Fernet:
        """Derive a Fernet key from the master key + salt via PBKDF2."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=_KDF_ITERATIONS,
        )
        key = base64.urlsafe_b64encode(kdf.derive(self._master_key))
        return Fernet(key)
