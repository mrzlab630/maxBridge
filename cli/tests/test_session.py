"""Tests for encrypted session storage."""

import json
import os
from pathlib import Path

from maxbridge.auth.session import Session


class TestSession:
    def test_save_and_load(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "test.session")
        s = Session(path, encryptor)
        assert not s.exists()

        s.save("dev123", "tok456")
        assert s.exists()
        assert s.device_id == "dev123"
        assert s.token == "tok456"

        # Verify encrypted on disk
        raw = open(path).read()
        assert "dev123" not in raw
        assert "tok456" not in raw

        # Load in new instance
        s2 = Session(path, encryptor)
        assert s2.load()
        assert s2.device_id == "dev123"
        assert s2.token == "tok456"
        assert s2.max_contact_id is None

    def test_loads_legacy_payload_without_identity(self, tmp_dir, encryptor):
        path = Path(tmp_dir) / "legacy.session"
        legacy = json.dumps({"device_id": "legacy-device", "token": "legacy-token"})
        path.write_text(encryptor.encrypt(legacy), encoding="utf-8")

        session = Session(str(path), encryptor)

        assert session.load() is True
        assert session.device_id == "legacy-device"
        assert session.token == "legacy-token"
        assert session.max_contact_id is None

    def test_identity_is_persisted_only_inside_encrypted_payload(self, tmp_dir, encryptor):
        path = Path(tmp_dir) / "identity.session"
        session = Session(str(path), encryptor)
        session.save("device", "token", "000123")

        raw = path.read_text(encoding="utf-8")
        assert "device" not in raw
        assert "token" not in raw
        assert "123" not in raw

        loaded = Session(str(path), encryptor)
        assert loaded.load() is True
        assert loaded.max_contact_id == "123"

    def test_malformed_optional_identity_does_not_break_session_load(
        self, tmp_dir, encryptor
    ):
        path = Path(tmp_dir) / "malformed-identity.session"
        payload = json.dumps({
            "device_id": "device",
            "token": "token",
            "max_contact_id": {"unexpected": "value"},
        })
        path.write_text(encryptor.encrypt(payload), encoding="utf-8")

        session = Session(str(path), encryptor)

        assert session.load() is True
        assert session.max_contact_id is None

    def test_file_permissions(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "perm.session")
        s = Session(path, encryptor)
        s.save("d", "t")
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_clear(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "clear.session")
        s = Session(path, encryptor)
        s.save("d", "t")
        s.clear()
        assert not s.exists()
        assert s.device_id is None
        assert s.token is None
        assert s.max_contact_id is None

    def test_load_nonexistent(self, tmp_dir, encryptor):
        s = Session(os.path.join(tmp_dir, "nope.session"), encryptor)
        assert not s.load()

    def test_load_corrupted(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "bad.session")
        with open(path, "w") as f:
            f.write("corrupted data")
        s = Session(path, encryptor)
        assert not s.load()
