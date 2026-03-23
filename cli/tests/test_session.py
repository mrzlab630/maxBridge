"""Tests for encrypted session storage."""

import os

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

    def test_load_nonexistent(self, tmp_dir, encryptor):
        s = Session(os.path.join(tmp_dir, "nope.session"), encryptor)
        assert not s.load()

    def test_load_corrupted(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "bad.session")
        with open(path, "w") as f:
            f.write("corrupted data")
        s = Session(path, encryptor)
        assert not s.load()
