"""Shared fixtures for maxBridge tests."""

import os
import tempfile

import pytest

from maxbridge.auth.encryption import TokenEncryptor


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture
def encryptor(tmp_dir):
    enc = TokenEncryptor(os.path.join(tmp_dir, "test.key"))
    enc.ensure_key()
    return enc


@pytest.fixture
def sample_packet():
    return {
        "opcode": 128,
        "payload": {
            "chatId": 12345,
            "message": {
                "id": "msg001",
                "text": "Hello test",
                "cid": 1711234567000,
                "sender": {"userId": 67890},
                "status": None,
                "attaches": [
                    {"_type": "PHOTO", "url": "https://example.com/photo.jpg"},
                ],
            },
        },
    }
