"""Tests for session token encryption."""

import os

import pytest

from maxbridge.auth.encryption import TokenEncryptor


class TestTokenEncryptor:
    def test_encrypt_decrypt_roundtrip(self, encryptor):
        secret = "my_super_secret_token"
        encrypted = encryptor.encrypt(secret)
        assert encrypted != secret
        assert ":" in encrypted
        assert encryptor.decrypt(encrypted) == secret

    def test_different_ciphertexts(self, encryptor):
        secret = "same_value"
        e1 = encryptor.encrypt(secret)
        e2 = encryptor.encrypt(secret)
        assert e1 != e2  # different salt
        assert encryptor.decrypt(e1) == secret
        assert encryptor.decrypt(e2) == secret

    def test_wrong_key_fails(self, tmp_dir):
        enc1 = TokenEncryptor(os.path.join(tmp_dir, "key1"))
        enc1.ensure_key()
        enc2 = TokenEncryptor(os.path.join(tmp_dir, "key2"))
        enc2.ensure_key()

        encrypted = enc1.encrypt("secret")
        with pytest.raises(ValueError, match="Decryption failed"):
            enc2.decrypt(encrypted)

    def test_key_file_permissions(self, tmp_dir):
        path = os.path.join(tmp_dir, "new.key")
        enc = TokenEncryptor(path)
        enc.ensure_key()
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_corrupted_ciphertext(self, encryptor):
        with pytest.raises(ValueError):
            encryptor.decrypt("garbage:data")

    def test_key_persistence(self, tmp_dir):
        path = os.path.join(tmp_dir, "persist.key")
        enc1 = TokenEncryptor(path)
        enc1.ensure_key()
        encrypted = enc1.encrypt("hello")

        enc2 = TokenEncryptor(path)
        enc2.ensure_key()
        assert enc2.decrypt(encrypted) == "hello"
