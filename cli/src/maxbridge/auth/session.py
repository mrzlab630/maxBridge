"""Session persistence — save/load device_id and login token with encryption."""

import json
import logging
import os
from pathlib import Path

from maxbridge.auth.encryption import TokenEncryptor

logger = logging.getLogger("maxbridge.auth.session")


class Session:
    """Manages MAX session data (device_id + login_token) on disk, encrypted."""

    def __init__(self, session_path: str, encryptor: TokenEncryptor) -> None:
        self.path = Path(session_path)
        self._encryptor = encryptor
        self.device_id: str | None = None
        self.token: str | None = None

    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def load(self) -> bool:
        """Load and decrypt session from file. Returns True if successful."""
        if not self.exists():
            return False
        try:
            encrypted = self.path.read_text(encoding="utf-8")
            plaintext = self._encryptor.decrypt(encrypted)
            data = json.loads(plaintext)
            self.device_id = data["device_id"]
            self.token = data["token"]
            logger.info("Session loaded and decrypted")
            return True
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning("Failed to load session: %s", e)
            return False

    def save(self, device_id: str, token: str) -> None:
        """Encrypt and persist session to file with atomic write."""
        self.device_id = device_id
        self.token = token
        self.path.parent.mkdir(parents=True, exist_ok=True)

        plaintext = json.dumps({"device_id": device_id, "token": token})
        encrypted = self._encryptor.encrypt(plaintext)

        # Atomic write: write to .tmp then rename
        tmp_path = self.path.with_suffix(".tmp")
        fd = os.open(str(tmp_path), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, encrypted.encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(str(tmp_path), str(self.path))
        logger.info("Session encrypted and saved")

    def clear(self) -> None:
        """Remove session file."""
        if self.path.exists():
            self.path.unlink()
        self.device_id = None
        self.token = None
        logger.info("Session cleared")
