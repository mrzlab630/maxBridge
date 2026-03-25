"""Тесты хранения аккаунтов."""

import os

from maxbridge.tui.accounts_store import (
    add_account,
    load_accounts,
    load_active,
    remove_account,
    save_accounts,
    save_active,
)


class TestAccountsStore:
    def test_load_empty(self, tmp_dir):
        path = os.path.join(tmp_dir, "acc.json")
        result = load_accounts({"default": {}}, path)
        assert "default" in result

    def test_save_and_load_dynamic(self, tmp_dir):
        path = os.path.join(tmp_dir, "acc.json")
        config_accounts = {"default": {"phone": "123"}}
        all_accounts = dict(config_accounts)
        all_accounts["work"] = {"phone": "456"}
        save_accounts(all_accounts, config_accounts, path)

        loaded = load_accounts(config_accounts, path)
        assert "default" in loaded
        assert "work" in loaded
        assert loaded["work"]["phone"] == "456"

    def test_config_accounts_not_saved(self, tmp_dir):
        path = os.path.join(tmp_dir, "acc.json")
        config_accounts = {"default": {"phone": "123"}}
        save_accounts(config_accounts, config_accounts, path)
        # Файл должен содержать пустой dict
        import json
        data = json.loads(open(path).read())
        assert data == {}

    def test_add_account(self, tmp_dir):
        path = os.path.join(tmp_dir, "acc.json")
        config = {"default": {}}
        accounts = dict(config)
        add_account(accounts, config, "new", {"phone": "789"}, path)
        assert "new" in accounts

        loaded = load_accounts(config, path)
        assert "new" in loaded

    def test_remove_account(self, tmp_dir):
        path = os.path.join(tmp_dir, "acc.json")
        config = {"default": {}}
        accounts = dict(config)
        accounts["temp"] = {"phone": "000"}
        save_accounts(accounts, config, path)

        remove_account(accounts, config, "temp", path)
        assert "temp" not in accounts
        loaded = load_accounts(config, path)
        assert "temp" not in loaded

    def test_config_priority(self, tmp_dir):
        """Config аккаунты имеют приоритет над сохранёнными."""
        path = os.path.join(tmp_dir, "acc.json")
        config = {"default": {"phone": "NEW"}}
        accounts = {"default": {"phone": "OLD"}, "extra": {}}
        save_accounts(accounts, {"default": {}}, path)

        loaded = load_accounts(config, path)
        assert loaded["default"]["phone"] == "NEW"
        assert "extra" in loaded


class TestActiveSession:
    def test_save_and_load(self, tmp_dir):
        path = os.path.join(tmp_dir, "active.txt")
        save_active("work", path)
        assert load_active(path) == "work"

    def test_load_nonexistent(self, tmp_dir):
        assert load_active(os.path.join(tmp_dir, "nope.txt")) is None

    def test_load_empty(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.txt")
        with open(path, "w") as f:
            f.write("")
        assert load_active(path) is None
