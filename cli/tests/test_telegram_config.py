"""Тесты конфига Telegram."""

import os

from maxbridge.telegram.config import (
    TelegramConfig,
    load_telegram_config,
    save_telegram_config,
)


class TestTelegramConfig:
    def test_default_config(self):
        cfg = TelegramConfig()
        assert cfg.enabled is False
        assert cfg.bot_token == ""
        assert cfg.chat_id == ""

    def test_save_and_load(self, tmp_dir):
        path = os.path.join(tmp_dir, "tg.json")
        cfg = TelegramConfig(
            enabled=True, bot_token="123:ABC", chat_id="-100123")
        save_telegram_config(cfg, path)

        loaded = load_telegram_config(path)
        assert loaded.enabled is True
        assert loaded.bot_token == "123:ABC"
        assert loaded.chat_id == "-100123"

    def test_file_permissions(self, tmp_dir):
        path = os.path.join(tmp_dir, "tg.json")
        save_telegram_config(TelegramConfig(), path)
        assert os.stat(path).st_mode & 0o777 == 0o600

    def test_load_nonexistent(self, tmp_dir):
        cfg = load_telegram_config(os.path.join(tmp_dir, "nope.json"))
        assert cfg.enabled is False

    def test_load_corrupted(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            f.write("{bad json")
        cfg = load_telegram_config(path)
        assert cfg.enabled is False

    def test_partial_config(self, tmp_dir):
        path = os.path.join(tmp_dir, "partial.json")
        with open(path, "w") as f:
            f.write('{"enabled": true}')
        cfg = load_telegram_config(path)
        assert cfg.enabled is True
        assert cfg.bot_token == ""
