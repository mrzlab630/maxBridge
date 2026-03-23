"""Конфиг Telegram оповещений — data/telegram.json."""

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("maxbridge.telegram.config")

_CONFIG_PATH = "data/telegram.json"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


def load_telegram_config(path: str = _CONFIG_PATH) -> TelegramConfig:
    """Загрузить конфиг. Возвращает дефолтный если файла нет."""
    p = Path(path)
    if not p.exists():
        return TelegramConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return TelegramConfig(
            enabled=bool(data.get("enabled", False)),
            bot_token=str(data.get("bot_token", "")),
            chat_id=str(data.get("chat_id", "")),
        )
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Ошибка чтения telegram конфига: %s", e)
        return TelegramConfig()


def save_telegram_config(config: TelegramConfig,
                         path: str = _CONFIG_PATH) -> None:
    """Сохранить конфиг с правами 0o600."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps(
            asdict(config), ensure_ascii=False, indent=2).encode())
    finally:
        os.close(fd)
