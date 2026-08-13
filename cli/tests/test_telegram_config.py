"""Тесты конфига Telegram."""

import os
import stat

import pytest

from maxbridge.telegram import config as config_module
from maxbridge.telegram.config import (
    TelegramConfig,
    load_telegram_config,
    load_telegram_config_snapshot,
    save_telegram_config,
)


class TestTelegramConfig:
    def test_default_config(self):
        cfg = TelegramConfig()
        assert cfg.enabled is False
        assert cfg.bot_token == ""
        assert cfg.chat_id == ""
        assert cfg.allowed_chat_ids is None

    def test_save_and_load(self, tmp_dir):
        path = os.path.join(tmp_dir, "tg.json")
        cfg = TelegramConfig(
            enabled=True,
            bot_token="123:ABC",
            chat_id="-100123",
            allowed_chat_ids=["-100123", "145"],
        )
        save_telegram_config(cfg, path)

        loaded = load_telegram_config(path)
        assert loaded.enabled is True
        assert loaded.bot_token == "123:ABC"
        assert loaded.chat_id == "-100123"
        assert loaded.allowed_chat_ids == ["-100123", "145"]

    def test_file_permissions(self, tmp_dir):
        path = os.path.join(tmp_dir, "tg.json")
        save_telegram_config(TelegramConfig(), path)
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_atomic_save_preserves_existing_file_when_publish_fails(self, tmp_path, monkeypatch):
        path = tmp_path / "tg.json"
        path.write_text('{"enabled": false}\n', encoding="utf-8")

        def fail_replace(_source, _destination):
            raise OSError("publish failed")

        monkeypatch.setattr(config_module.os, "replace", fail_replace)

        with pytest.raises(OSError, match="publish failed"):
            save_telegram_config(TelegramConfig(enabled=True), path)

        assert path.read_text(encoding="utf-8") == '{"enabled": false}\n'
        assert not list(tmp_path.glob(".tg.json.*.tmp"))

    def test_atomic_save_replaces_symlink_without_mutating_target(self, tmp_path):
        target = tmp_path / "external.json"
        target.write_text('{"owner": "external"}\n', encoding="utf-8")
        path = tmp_path / "tg.json"
        path.symlink_to(target)

        save_telegram_config(TelegramConfig(enabled=True), path)

        assert not path.is_symlink()
        assert target.read_text(encoding="utf-8") == '{"owner": "external"}\n'
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_directory_fsync_failure_after_publish_is_warning_only(
        self, tmp_path, monkeypatch, caplog
    ):
        path = tmp_path / "tg.json"
        original_fsync = config_module.os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("secret directory detail")
            original_fsync(fd)

        monkeypatch.setattr(config_module.os, "fsync", fail_directory_fsync)

        save_telegram_config(
            TelegramConfig(enabled=True, bot_token="secret-token", chat_id="secret-chat"),
            path,
        )

        assert load_telegram_config(path).enabled is True
        assert "Telegram config published but directory sync failed: OSError" in caplog.text
        assert "secret directory detail" not in caplog.text
        assert "secret-token" not in caplog.text
        assert str(path) not in caplog.text

    def test_file_fsync_failure_before_publish_still_raises(
        self, tmp_path, monkeypatch
    ):
        path = tmp_path / "tg.json"
        path.write_text('{"enabled": false}\n', encoding="utf-8")

        def fail_file_fsync(_fd):
            raise OSError("file sync failed")

        monkeypatch.setattr(config_module.os, "fsync", fail_file_fsync)

        with pytest.raises(OSError, match="file sync failed"):
            save_telegram_config(TelegramConfig(enabled=True), path)

        assert path.read_text(encoding="utf-8") == '{"enabled": false}\n'

    def test_load_nonexistent(self, tmp_dir):
        cfg = load_telegram_config(os.path.join(tmp_dir, "nope.json"))
        assert cfg.enabled is False

    def test_load_corrupted(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{bad json")
        cfg = load_telegram_config(path)
        assert cfg.enabled is False

    def test_snapshot_marks_invalid_without_exposing_contents(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text('{"bot_token": "super-secret",', encoding="utf-8")

        snapshot = load_telegram_config_snapshot(path)

        assert snapshot.config is None
        assert snapshot.fingerprint.startswith("invalid:")
        assert snapshot.error == "invalid JSON config: JSONDecodeError"
        assert "super-secret" not in snapshot.error

    def test_partial_config(self, tmp_dir):
        path = os.path.join(tmp_dir, "partial.json")
        with open(path, "w") as f:
            f.write('{"enabled": true}')
        cfg = load_telegram_config(path)
        assert cfg.enabled is True
        assert cfg.bot_token == ""
        assert cfg.allowed_chat_ids == []

    def test_chat_id_used_as_default_allowed_chat(self, tmp_dir):
        path = os.path.join(tmp_dir, "single-chat.json")
        with open(path, "w") as f:
            f.write('{"enabled": true, "chat_id": "-100123"}')
        cfg = load_telegram_config(path)
        assert cfg.allowed_chat_ids == ["-100123"]
