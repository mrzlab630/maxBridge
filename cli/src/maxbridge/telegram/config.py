"""Конфиг Telegram оповещений — data/telegram.json."""

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path

logger = logging.getLogger("maxbridge.telegram.config")

_CONFIG_PATH = "data/telegram.json"


@dataclass
class TelegramConfig:
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    allowed_chat_ids: list[str] | None = None


@dataclass(frozen=True)
class TelegramConfigSnapshot:
    config: TelegramConfig | None
    fingerprint: str
    error: str | None = None


def telegram_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Return an explicit config path or resolve the default against runtime cwd."""
    selected = path if path is not None else _CONFIG_PATH
    return Path(os.path.abspath(Path(selected).expanduser()))


def load_telegram_config_snapshot(
    path: str | os.PathLike[str] = _CONFIG_PATH,
) -> TelegramConfigSnapshot:
    """Read one validated config snapshot without replacing invalid state."""
    p = telegram_config_path(path)
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        return TelegramConfigSnapshot(None, "missing", "file is missing")
    except OSError as exc:
        return TelegramConfigSnapshot(
            None,
            f"read-error:{type(exc).__name__}",
            f"read failed: {type(exc).__name__}",
        )

    fingerprint = sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("root must be an object")
        config = TelegramConfig(
            enabled=bool(data.get("enabled", False)),
            bot_token=str(data.get("bot_token", "")),
            chat_id=str(data.get("chat_id", "")),
            allowed_chat_ids=_load_allowed_chat_ids(data),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
        return TelegramConfigSnapshot(
            None,
            f"invalid:{fingerprint}",
            f"invalid JSON config: {type(exc).__name__}",
        )
    return TelegramConfigSnapshot(config, fingerprint)


def load_telegram_config(
    path: str | os.PathLike[str] = _CONFIG_PATH,
) -> TelegramConfig:
    """Load config, returning disabled defaults when no valid file exists."""
    snapshot = load_telegram_config_snapshot(path)
    if snapshot.config is None:
        if snapshot.error != "file is missing":
            logger.warning("Ошибка чтения telegram конфига: %s", snapshot.error)
        return TelegramConfig()
    return snapshot.config


def save_telegram_config(
    config: TelegramConfig,
    path: str | os.PathLike[str] = _CONFIG_PATH,
) -> None:
    """Atomically publish config with private permissions."""
    p = telegram_config_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = -1
            json.dump(asdict(config), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, p)
        try:
            directory_fd = os.open(p.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            logger.warning(
                "Telegram config published but directory sync failed: %s",
                type(exc).__name__,
            )
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _load_allowed_chat_ids(data: dict) -> list[str]:
    raw = data.get("allowed_chat_ids")
    if isinstance(raw, list):
        values = [str(item) for item in raw if str(item)]
        if values:
            return values

    chat_id = str(data.get("chat_id", ""))
    return [chat_id] if chat_id else []
