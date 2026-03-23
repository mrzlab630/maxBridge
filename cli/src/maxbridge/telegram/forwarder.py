"""Пересылка сообщений MAX → Telegram через Bot API."""

import logging

import aiohttp

from maxbridge.bridge.event_bus import EventBus
from maxbridge.client.account_manager import AccountManager
from maxbridge.telegram.config import TelegramConfig, load_telegram_config
from maxbridge.utils.types import MessageStatus, UnifiedMessage

logger = logging.getLogger("maxbridge.telegram.forwarder")

_API = "https://api.telegram.org/bot{token}/{method}"
_SUB_ID = "telegram_forwarder"
_FILE_LIMIT = 50 * 1024 * 1024
_MAX_CAPTION = 1024
_MAX_TEXT = 4096


class TelegramForwarder:
    """Подписчик EventBus — пересылает сообщения в Telegram."""

    def __init__(self, event_bus: EventBus,
                 account_manager: AccountManager) -> None:
        self._bus = event_bus
        self._manager = account_manager
        self._config: TelegramConfig | None = None
        self._http: aiohttp.ClientSession | None = None
        self._multi = len(account_manager.account_ids) > 1

    async def start(self) -> None:
        self._config = load_telegram_config()
        if not self._config or not self._config.enabled:
            logger.info("Telegram оповещения отключены")
            return
        if not self._config.bot_token or not self._config.chat_id:
            logger.warning("Telegram: не задан bot_token или chat_id")
            return
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30))
        self._bus.subscribe(_SUB_ID, self._on_message)
        logger.info("Telegram оповещения включены (chat_id=%s)",
                     self._config.chat_id)

    async def stop(self) -> None:
        if self._bus.has_subscriber(_SUB_ID):
            self._bus.unsubscribe(_SUB_ID)
        if self._http and not self._http.closed:
            await self._http.close()
            self._http = None

    async def reload(self) -> None:
        await self.stop()
        await self.start()

    async def _on_message(self, msg: UnifiedMessage) -> None:
        if not self._config or not self._http:
            return
        if msg.status == MessageStatus.DELETED:
            return

        caption = self._format_header(msg)
        media = self._find_media(msg)

        if media:
            sent = await self._send_media(media, caption, msg.text)
            if sent:
                return

        # Fallback: текст
        text = self._format_full(msg)
        await self._send_text(text)

    # ── Форматирование ──────────────────────────────────

    def _format_header(self, msg: UnifiedMessage) -> str:
        """Заголовок: отправитель → чат."""
        parts = []
        if msg.status == MessageStatus.EDITED:
            parts.append("[ред.] ")
        sender = _esc(msg.sender_name or str(msg.sender_id or "?"))
        chat = _esc(msg.chat_name or str(msg.chat_id))
        parts.append(f"<b>{sender}</b> → <b>{chat}</b>")
        if self._multi:
            parts.append(f" [{_esc(msg.account_id)}]")
        return "".join(parts)

    def _format_full(self, msg: UnifiedMessage) -> str:
        """Полное сообщение с текстом и описанием вложений."""
        parts = [self._format_header(msg), "\n"]
        if msg.text:
            parts.append(_esc(msg.text))
        for a in msg.attachments:
            atype = a.get("type", "?")
            data = a.get("data", {})
            if atype == "AUDIO":
                dur = data.get("duration", 0)
                secs = dur // 1000 if dur > 100 else dur
                parts.append(f"\n🎤 Голосовое ({secs}с)")
            elif atype == "VIDEO":
                dur = data.get("duration", 0)
                secs = dur // 1000 if dur > 100 else dur
                parts.append(f"\n🎬 Видео ({secs}с)")
            else:
                name = data.get("fileName") or data.get("name") or atype
                parts.append(f"\n📎 {_esc(name)}")
        return "".join(parts)[:_MAX_TEXT]

    def _find_media(self, msg: UnifiedMessage) -> dict | None:
        """Найти первое пересылаемое вложение."""
        for a in msg.attachments:
            atype = a.get("type", "")
            data = a.get("data", {})
            url = self._extract_url(atype, data)
            if url:
                return {"type": atype, "url": url, "data": data}
        return None

    @staticmethod
    def _extract_url(atype: str, data: dict) -> str:
        """Извлечь URL для скачивания из вложения MAX.

        Только URL которые не привязаны к IP (baseUrl, thumbnail).
        AUDIO/FILE url привязаны к IP и не скачиваются с другого сервера.
        """
        if atype == "PHOTO":
            return data.get("baseUrl") or ""
        if atype == "VIDEO":
            return data.get("thumbnail") or ""
        # AUDIO и FILE url привязаны к srcIp — не работают
        return ""

    # ── Отправка медиа ──────────────────────────────────

    async def _send_media(self, media: dict, caption: str,
                          text: str) -> bool:
        """Скачать файл и отправить в Telegram."""
        atype = media["type"]
        url = media["url"]
        try:
            file_bytes = await self._download(url)
            if not file_bytes:
                return False

            full_caption = caption
            if text:
                full_caption += f"\n{_esc(text)}"
            full_caption = full_caption[:_MAX_CAPTION]

            if atype == "PHOTO":
                return await self._tg_send_file(
                    "sendPhoto", "photo", file_bytes,
                    "photo.jpg", full_caption)
            elif atype == "VIDEO":
                # Thumbnail — отправляем как фото с пометкой
                return await self._tg_send_file(
                    "sendPhoto", "photo", file_bytes,
                    "video_preview.jpg",
                    f"🎬 {full_caption}")
            elif atype == "AUDIO":
                return await self._tg_send_file(
                    "sendVoice", "voice", file_bytes,
                    "voice.ogg", full_caption)
            elif atype == "FILE":
                name = media["data"].get("fileName") or "file"
                return await self._tg_send_file(
                    "sendDocument", "document", file_bytes,
                    name, full_caption)
        except Exception:
            logger.debug("Ошибка отправки медиа", exc_info=True)
        return False

    async def _download(self, url: str) -> bytes | None:
        """Скачать файл по URL."""
        try:
            async with self._http.get(url) as resp:
                if resp.status != 200:
                    return None
                if (resp.content_length or 0) > _FILE_LIMIT:
                    return None
                data = await resp.read()
                if len(data) > _FILE_LIMIT:
                    return None
                return data
        except Exception:
            return None

    async def _tg_send_file(self, method: str, field: str,
                            file_bytes: bytes, filename: str,
                            caption: str) -> bool:
        """Отправить файл в Telegram."""
        form = aiohttp.FormData()
        form.add_field("chat_id", self._config.chat_id)
        form.add_field(field, file_bytes, filename=filename)
        form.add_field("caption", caption)
        form.add_field("parse_mode", "HTML")

        api = _API.format(token=self._config.bot_token, method=method)
        try:
            async with self._http.post(api, data=form) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram %s: %s %s",
                                   method, resp.status, body[:100])
                    return False
            return True
        except Exception:
            logger.debug("Ошибка Telegram %s", method, exc_info=True)
            return False

    # ── Отправка текста ─────────────────────────────────

    async def _send_text(self, text: str) -> None:
        if not self._config or not self._http:
            return
        api = _API.format(token=self._config.bot_token,
                          method="sendMessage")
        payload = {
            "chat_id": self._config.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            async with self._http.post(api, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("Telegram sendMessage: %s %s",
                                   resp.status, body[:100])
        except Exception:
            logger.debug("Ошибка Telegram sendMessage", exc_info=True)


def _esc(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))
