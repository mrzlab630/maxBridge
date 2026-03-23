"""Хранение реестра динамических аккаунтов на диске."""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("maxbridge.tui.accounts_store")

_DEFAULT_PATH = "data/accounts.json"
_ACTIVE_PATH = "data/active_session.txt"


def load_accounts(config_accounts: dict[str, Any],
                  store_path: str = _DEFAULT_PATH) -> dict[str, Any]:
    """Загрузить аккаунты: конфиг + сохранённые динамические.

    Конфиг имеет приоритет при конфликте имён.
    """
    result = dict(config_accounts)
    p = Path(store_path)
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8"))
            for aid, cfg in stored.items():
                if aid not in result:
                    result[aid] = cfg
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Ошибка чтения %s: %s", store_path, e)
    return result


def save_accounts(accounts: dict[str, Any],
                  config_accounts: dict[str, Any],
                  store_path: str = _DEFAULT_PATH) -> None:
    """Сохранить только динамические аккаунты (не из конфига)."""
    dynamic = {
        aid: cfg for aid, cfg in accounts.items()
        if aid not in config_accounts
    }
    p = Path(store_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(dynamic, ensure_ascii=False, indent=2).encode())
    finally:
        os.close(fd)


def add_account(accounts: dict[str, Any],
                config_accounts: dict[str, Any],
                aid: str, cfg: dict[str, Any],
                store_path: str = _DEFAULT_PATH) -> None:
    """Добавить аккаунт и сохранить на диск."""
    accounts[aid] = cfg
    save_accounts(accounts, config_accounts, store_path)


def remove_account(accounts: dict[str, Any],
                   config_accounts: dict[str, Any],
                   aid: str,
                   store_path: str = _DEFAULT_PATH) -> None:
    """Удалить аккаунт и сохранить на диск."""
    accounts.pop(aid, None)
    save_accounts(accounts, config_accounts, store_path)


def save_active(aid: str, path: str = _ACTIVE_PATH) -> None:
    """Сохранить ID активной сессии."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(aid, encoding="utf-8")


def load_active(path: str = _ACTIVE_PATH) -> str | None:
    """Загрузить ID активной сессии."""
    p = Path(path)
    if p.exists():
        aid = p.read_text(encoding="utf-8").strip()
        return aid if aid else None
    return None
