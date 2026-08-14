"""Общие хелперы для TUI."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.session import Session
from maxbridge.auth.token_auth import login_with_token
from maxbridge.protocol.max_client import MaxClient

_MSK = timezone(timedelta(hours=3))


def format_ts(ts) -> str:
    """Форматирование MAX timestamp (мс) в московское время."""
    if not ts:
        return "-"
    try:
        dt = datetime.fromtimestamp(int(ts) / 1000, tz=_MSK)
        return dt.strftime("%d.%m.%Y %H:%M:%S MSK")
    except (ValueError, TypeError, OSError):
        return str(ts)


def format_phone(phone) -> str:
    """Форматирование телефона: +79030605090."""
    s = str(phone)
    if not s.startswith("+"):
        s = "+" + s
    return s


def mask_device_id(device_id: str) -> str:
    """Маскировка device_id: abc1...f9de."""
    if not device_id or len(device_id) < 8:
        return device_id or "?"
    return device_id[:4] + "..." + device_id[-4:]


def resolve_session_path(aid: str, cfg: dict) -> str:
    """Единый путь к файлу сессии."""
    return cfg.get("session_file", f"data/{aid}.session")


def session_exists(aid: str, cfg: dict) -> bool:
    """Проверка существования файла сессии."""
    p = Path(resolve_session_path(aid, cfg))
    return p.exists() and p.stat().st_size > 0


def load_session(aid: str, cfg: dict,
                 encryptor: TokenEncryptor) -> Session | None:
    """Загрузить сессию. Возвращает None если нет или невалидна."""
    sess = Session(resolve_session_path(aid, cfg), encryptor)
    if sess.load():
        return sess
    return None


async def connect_and_login(
    session: Session,
) -> tuple[MaxClient, dict]:
    """Создать клиент, подключить, авторизовать.

    Возвращает (client, login_response). Вызывающий отвечает за disconnect.
    """
    client = MaxClient()
    await client.connect()
    resp = await login_with_token(client, session)
    return client, resp
