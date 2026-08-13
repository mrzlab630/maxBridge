"""Пересылка сообщений MAX → Telegram через Bot API."""

import asyncio
import logging
from pathlib import Path

import aiohttp

from maxbridge.bridge.event_bus import EventBus
from maxbridge.client.account_manager import AccountManager
from maxbridge.media.uploader import get_download_url
from maxbridge.telegram.config import (
    TelegramConfig,
    TelegramConfigSnapshot,
    load_telegram_config_snapshot,
    telegram_config_path,
)
from maxbridge.utils.types import MessageStatus, UnifiedMessage

logger = logging.getLogger("maxbridge.telegram.forwarder")

_API = "https://api.telegram.org/bot{token}/{method}"
_SUB_ID = "telegram_forwarder"
_FILE_LIMIT = 50 * 1024 * 1024
_MAX_CAPTION = 1024
_MAX_TEXT = 4096
_MAX_ALERT_BODY = 3800


class TelegramLogHandler(logging.Handler):
    """Forward maxBridge errors to Telegram without looping on self-errors."""

    def __init__(self, forwarder: "TelegramForwarder",
                 level: int = logging.ERROR) -> None:
        super().__init__(level)
        self._forwarder = forwarder

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("maxbridge.telegram.forwarder"):
            return
        try:
            rendered = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._forwarder.send_alert_nowait(rendered)


class TelegramForwarder:
    """Подписчик EventBus — пересылает сообщения в Telegram."""

    def __init__(self, event_bus: EventBus,
                 account_manager: AccountManager,
                 config_path: str | Path = "data/telegram.json") -> None:
        self._bus = event_bus
        self._manager = account_manager
        self._config_path = telegram_config_path(config_path)
        self._config: TelegramConfig | None = None
        self._http: aiohttp.ClientSession | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._observed_fingerprint: str | None = None
        self._reload_lock = asyncio.Lock()
        self._multi = len(account_manager.account_ids) > 1

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        snapshot = load_telegram_config_snapshot(self._config_path)
        await self.reload(snapshot, initial=True)

    async def stop(self) -> None:
        async with self._reload_lock:
            await self._deactivate()
            self._loop = None

    async def _deactivate(self, *, close_http: bool = True) -> None:
        if self._bus.has_subscriber(_SUB_ID):
            self._bus.unsubscribe(_SUB_ID)
        if close_http and self._http and not self._http.closed:
            await self._http.close()
        if close_http:
            self._http = None

    async def reload(
        self,
        snapshot: TelegramConfigSnapshot | None = None,
        *,
        initial: bool = False,
    ) -> bool:
        """Apply one valid semantic config change, preserving last-known-good state."""
        snapshot = snapshot or load_telegram_config_snapshot(self._config_path)
        async with self._reload_lock:
            if not initial and snapshot.fingerprint == self._observed_fingerprint:
                return False
            if snapshot.config is None:
                self._observed_fingerprint = snapshot.fingerprint
                if initial and self._config is None:
                    self._config = TelegramConfig()
                if not initial:
                    logger.warning(
                        "Telegram config reload skipped; keeping last-known-good state: %s",
                        snapshot.error,
                    )
                return False

            config = snapshot.config
            should_be_ready = bool(config.enabled and config.bot_token and config.chat_id)
            is_inactive = self._http is None and not self._bus.has_subscriber(_SUB_ID)
            if self._config == config and (
                (should_be_ready and self.is_ready) or (not should_be_ready and is_inactive)
            ):
                self._observed_fingerprint = snapshot.fingerprint
                return False

            reuse_http = bool(should_be_ready and self._http and not self._http.closed)
            new_http = self._http if reuse_http else None
            if should_be_ready and new_http is None:
                new_http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))

            try:
                await self._deactivate(close_http=not reuse_http)
            except BaseException:
                if not reuse_http and new_http and not new_http.closed:
                    await new_http.close()
                raise
            self._config = config
            self._http = new_http
            if not config.enabled:
                logger.info("Telegram оповещения отключены")
            elif not config.bot_token or not config.chat_id:
                logger.warning("Telegram: не задан bot_token или chat_id")
            else:
                self._bus.subscribe(_SUB_ID, self._on_message)
                logger.info("Telegram оповещения включены (chat_id=%s)", config.chat_id)
            self._observed_fingerprint = snapshot.fingerprint
            return True

    @property
    def observed_fingerprint(self) -> str | None:
        return self._observed_fingerprint

    @property
    def is_ready(self) -> bool:
        return bool(
            self._config
            and self._config.enabled
            and self._http
            and not self._http.closed
            and self._loop
            and self._bus.has_subscriber(_SUB_ID)
        )

    def make_log_handler(self, fmt: str) -> logging.Handler:
        handler = TelegramLogHandler(self)
        handler.setFormatter(logging.Formatter(fmt))
        return handler

    async def _on_message(self, msg: UnifiedMessage) -> None:
        if not self._config or not self._http:
            return
        if msg.status == MessageStatus.DELETED:
            return

        media_items = self._collect_media(msg)

        if media_items:
            sent_count, failed = await self._send_media_items(media_items, msg)
            if sent_count == len(media_items):
                return
            if failed:
                await self._send_text(self._format_failed_attachments(msg, failed))
                return
        elif self._has_non_control_attachments(msg):
            self._log_unforwardable_attachments(msg)

        # Fallback: текст
        text = self._format_full(msg)
        await self._send_text(text)

    async def send_alert(self, text: str, title: str = "Ошибка maxBridge") -> bool:
        """Отправить системное уведомление в Telegram."""
        if not self._config or not self._http:
            return False
        return await self._send_text(self._format_alert(title, text))

    def send_alert_nowait(self, text: str, title: str = "Ошибка maxBridge") -> None:
        """Поставить отправку уведомления в event loop, если Telegram уже поднят."""
        if not self.is_ready or not self._loop or self._loop.is_closed():
            return

        def _schedule() -> None:
            asyncio.create_task(self.send_alert(text, title=title))

        self._loop.call_soon_threadsafe(_schedule)

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
        control_text = self._format_control_message(msg)
        if control_text:
            return control_text[:_MAX_TEXT]

        body_lines = self._format_body_lines(msg)
        if not body_lines:
            return self._format_header(msg)[:_MAX_TEXT]
        full_text = f"{self._format_header(msg)}\n" + "\n".join(body_lines)
        return full_text[:_MAX_TEXT]

    def _format_control_message(self, msg: UnifiedMessage) -> str | None:
        """Readable fallback for service actions like join/call/pin."""
        if msg.text.strip():
            return None
        if not msg.attachments:
            return None
        if any(a.get("type") != "CONTROL" for a in msg.attachments):
            return None

        sender = _esc(msg.sender_name or str(msg.sender_id or "Пользователь"))
        chat = _esc(msg.chat_name or str(msg.chat_id))
        events = []
        for attach in msg.attachments:
            data = attach.get("data", {})
            event = data.get("event")
            if event:
                events.append(_humanize_control_event(str(event)))

        if not events:
            events = ["системное действие"]

        action_text = ", ".join(events)
        parts = [f"<b>{sender}</b> совершил(а) действие в <b>{chat}</b>: { _esc(action_text) }"]
        if self._multi:
            parts.append(f" [{_esc(msg.account_id)}]")
        return "".join(parts)

    def _find_media(self, msg: UnifiedMessage) -> dict | None:
        """Найти первое пересылаемое вложение."""
        media_items = self._collect_media(msg)
        if media_items:
            return media_items[0]
        return None

    def _collect_media(self, msg: UnifiedMessage) -> list[dict]:
        """Collect every downloadable attachment that should be sent to Telegram."""
        media_items = self._collect_media_in_attachments(msg.attachments)
        if msg.link_type == "FORWARD" and msg.linked_message is not None:
            media_items.extend(
                self._collect_media_in_attachments(msg.linked_message.attachments),
            )
        return media_items

    def _find_media_in_attachments(self,
                                   attachments: list[dict[str, object]]) -> dict | None:
        """Find the first downloadable media attachment in a list."""
        media_items = self._collect_media_in_attachments(attachments)
        return media_items[0] if media_items else None

    def _collect_media_in_attachments(self,
                                      attachments: list[dict[str, object]]) -> list[dict]:
        """Find every downloadable media attachment in a list."""
        media_items = []
        for attachment in attachments:
            atype = str(attachment.get("type", ""))
            data = attachment.get("data", {})
            if not isinstance(data, dict):
                continue
            if atype == "SHARE":
                image = data.get("image")
                if isinstance(image, dict):
                    nested_type = str(image.get("_type", "PHOTO"))
                    url = self._extract_url(nested_type, image)
                    if url:
                        media_items.append({
                            "type": nested_type,
                            "url": url,
                            "file_id": self._extract_file_id(image),
                            "data": image,
                        })
                continue

            url = self._extract_url(atype, data)
            file_id = self._extract_file_id(data)
            if not url and atype not in {"PHOTO", "VIDEO"}:
                url = self._extract_file_url(data)
            if url or file_id is not None:
                media_items.append({
                    "type": atype,
                    "url": url,
                    "file_id": file_id,
                    "data": data,
                })
        return media_items

    @staticmethod
    def _extract_url(atype: str, data: dict) -> str:
        """Извлечь URL для скачивания из вложения MAX.

        Только URL которые указывают на само медиа. Thumbnail для VIDEO не
        подходит: Telegram тогда получает картинку вместо видео.
        AUDIO/FILE url привязаны к IP и не скачиваются с другого сервера.
        """
        if atype == "PHOTO":
            return TelegramForwarder._first_str(data, "baseUrl", "url")
        if atype == "VIDEO":
            return TelegramForwarder._first_str(
                data,
                "baseUrl",
                "url",
                "downloadUrl",
                "videoUrl",
            )
        if atype == "SHARE":
            image = data.get("image")
            if isinstance(image, dict):
                return TelegramForwarder._extract_url(
                    str(image.get("_type", "PHOTO")), image,
                )
        # AUDIO и FILE url привязаны к srcIp — не работают
        return ""

    @staticmethod
    def _extract_file_url(data: dict) -> str:
        """Best-effort direct URL fallback for document-like attachments."""
        return TelegramForwarder._first_str(
            data,
            "downloadUrl",
            "fileUrl",
            "url",
            "baseUrl",
        )

    @staticmethod
    def _first_str(data: dict, *keys: str) -> str:
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    @staticmethod
    def _extract_file_id(data: dict) -> int | None:
        """Extract numeric MAX file id used by DOWNLOAD_VIDEO/DOWNLOAD_FILE."""
        for key in ("fileId", "file_id", "videoId", "video_id", "id"):
            value = data.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)

        for key in ("file", "video", "media", "document"):
            nested = data.get(key)
            if isinstance(nested, dict):
                value = TelegramForwarder._extract_file_id(nested)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _format_alert(title: str, text: str) -> str:
        """Подготовить системное уведомление об ошибке."""
        header = f"⚠️ <b>{_esc(title)}</b>"
        body = (_esc(text).strip() or "Unknown error")[:_MAX_ALERT_BODY]
        return f"{header}\n<pre>{body}</pre>"

    # ── Отправка медиа ──────────────────────────────────

    async def _send_media_items(self, media_items: list[dict],
                                msg: UnifiedMessage) -> tuple[int, list[dict]]:
        """Send all media attachments, putting the message caption on first success."""
        sent_count = 0
        failed = []
        caption = self._format_media_caption(msg)[:_MAX_CAPTION]
        for media in media_items:
            media_caption = caption if sent_count == 0 else ""
            sent = await self._send_media(media, msg, caption=media_caption)
            if sent:
                sent_count += 1
            else:
                failed.append(media)
        return sent_count, failed

    async def _send_media(self, media: dict, msg: UnifiedMessage,
                          caption: str | None = None) -> bool:
        """Скачать файл и отправить в Telegram."""
        atype = media["type"]
        try:
            url = await self._resolve_media_url(media, msg)
            if not url:
                self._log_media_failure("download URL missing", media, msg)
                return False

            file_bytes = await self._download(url)
            if not file_bytes:
                self._log_media_failure("download failed", media, msg)
                return False

            full_caption = self._format_full(msg)[:_MAX_CAPTION] if caption is None else caption
            data = media.get("data", {})
            if not isinstance(data, dict):
                data = {}

            if atype == "PHOTO":
                return await self._tg_send_file(
                    "sendPhoto", "photo", file_bytes,
                    "photo.jpg", full_caption)
            elif atype == "VIDEO":
                return await self._tg_send_file(
                    "sendVideo", "video", file_bytes,
                    self._video_filename(data), full_caption)
            elif atype == "AUDIO":
                return await self._tg_send_file(
                    "sendVoice", "voice", file_bytes,
                    self._audio_filename(data), full_caption)
            else:
                name = self._document_filename(atype, data)
                return await self._tg_send_file(
                    "sendDocument", "document", file_bytes,
                    name, full_caption)
        except Exception:
            logger.debug("Ошибка отправки медиа", exc_info=True)
        return False

    async def _resolve_media_url(self, media: dict, msg: UnifiedMessage) -> str:
        """Resolve direct or MAX-generated download URL for any supported media."""
        atype = str(media.get("type") or "")
        url = str(media.get("url") or "")
        if atype in {"PHOTO", "VIDEO"} and url:
            return url

        file_id = media.get("file_id")
        if not isinstance(file_id, int):
            data = media.get("data", {})
            if isinstance(data, dict):
                file_id = self._extract_file_id(data)
        if not isinstance(file_id, int):
            return url

        if isinstance(file_id, int):
            account = self._manager.get(msg.account_id)
            if account is not None and account.is_connected:
                media_type = "video" if atype == "VIDEO" else "file"
                try:
                    resolved = await get_download_url(
                        account.connection,
                        msg.chat_id,
                        msg.message_id,
                        file_id,
                        media_type,
                    )
                    if resolved:
                        return resolved
                except Exception:
                    logger.debug("Не удалось получить URL вложения MAX", exc_info=True)

        return url

    @staticmethod
    def _has_non_control_attachments(msg: UnifiedMessage) -> bool:
        attachments = list(msg.attachments)
        if msg.link_type == "FORWARD" and msg.linked_message is not None:
            attachments.extend(msg.linked_message.attachments)
        return any(attachment.get("type") != "CONTROL" for attachment in attachments)

    def _log_unforwardable_attachments(self, msg: UnifiedMessage) -> None:
        summaries = []
        for attachment in msg.attachments:
            summaries.append(self._attachment_summary(attachment))
        if msg.link_type == "FORWARD" and msg.linked_message is not None:
            for attachment in msg.linked_message.attachments:
                summaries.append(f"forward:{self._attachment_summary(attachment)}")
        logger.warning(
            "MAX message has attachments but no downloadable file metadata: "
            "account=%s chat=%s message=%s attachments=%s",
            msg.account_id,
            msg.chat_id,
            msg.message_id or "?",
            "; ".join(summaries)[:1000],
        )

    @staticmethod
    def _attachment_summary(attachment: dict[str, object]) -> str:
        atype = str(attachment.get("type", "?"))
        data = attachment.get("data", {})
        if not isinstance(data, dict):
            return f"type={atype} keys=-"
        keys = ",".join(sorted(str(key) for key in data.keys())[:10]) or "-"
        name = (
            data.get("fileName")
            or data.get("name")
            or data.get("title")
            or data.get("filename")
            or "-"
        )
        return f"type={atype} name={name} keys={keys}"

    async def _resolve_video_url(self, media: dict, msg: UnifiedMessage) -> str:
        """Resolve a real video download URL, never the preview thumbnail."""
        return await self._resolve_media_url(media, msg)

    @staticmethod
    def _video_filename(data: dict) -> str:
        name = data.get("fileName") or data.get("name") or "video.mp4"
        filename = str(name)
        if "." not in filename:
            return f"{filename}.mp4"
        return filename

    @staticmethod
    def _audio_filename(data: dict) -> str:
        name = data.get("fileName") or data.get("name") or "voice.ogg"
        filename = str(name)
        if "." not in filename:
            return f"{filename}.ogg"
        return filename

    @staticmethod
    def _document_filename(atype: str, data: dict) -> str:
        name = (
            data.get("fileName")
            or data.get("name")
            or data.get("title")
            or data.get("filename")
            or atype.lower()
            or "file"
        )
        return str(name)

    def _format_failed_attachments(self, msg: UnifiedMessage,
                                   failed: list[dict]) -> str:
        """Short operator-visible notice when only part of a bundle was sent."""
        lines = self._format_media_caption(msg).splitlines()
        lines.append(f"⚠️ Не удалось отправить вложения: {len(failed)}")
        for media in failed[:10]:
            lines.append(f"• {_esc(self._media_display_name(media))}")
        return "\n".join(lines)[:_MAX_TEXT]

    def _format_media_caption(self, msg: UnifiedMessage) -> str:
        """Caption for successfully forwarded files without attachment placeholders."""
        lines = [self._format_header(msg)]
        if msg.link_type == "REPLY":
            lines.extend(self._format_linked_text_block(msg))
        if msg.text:
            lines.append(_esc(msg.text))
        if msg.link_type != "REPLY":
            lines.extend(self._format_linked_text_block(msg))
        return "\n".join(lines)[:_MAX_TEXT]

    def _format_linked_text_block(self, msg: UnifiedMessage) -> list[str]:
        """Render linked message context without attachment placeholder lines."""
        if not msg.link_type:
            return []
        lines = [self._link_label(msg.link_type)]
        linked = msg.linked_message
        if linked is not None and linked.text:
            lines.append(_esc(linked.text))
        if len(lines) == 1 and linked is None:
            lines.append("<i>[без содержимого]</i>")
        return lines

    @staticmethod
    def _media_display_name(media: dict) -> str:
        atype = str(media.get("type") or "FILE")
        data = media.get("data", {})
        if not isinstance(data, dict):
            data = {}
        name = (
            data.get("fileName")
            or data.get("name")
            or data.get("title")
            or data.get("filename")
        )
        return str(name or atype)

    def _log_media_failure(self, reason: str, media: dict,
                           msg: UnifiedMessage) -> None:
        data = media.get("data", {})
        keys = "-"
        if isinstance(data, dict):
            keys = ",".join(sorted(str(key) for key in data.keys())[:10]) or "-"
        logger.warning(
            "MAX attachment was not forwarded: reason=%s account=%s chat=%s "
            "message=%s type=%s name=%s keys=%s",
            reason,
            msg.account_id,
            msg.chat_id,
            msg.message_id or "?",
            media.get("type", "?"),
            self._media_display_name(media),
            keys,
        )

    def _format_body_lines(self, msg: UnifiedMessage) -> list[str]:
        """Render wrapper text, attachments, and linked content into body lines."""
        lines = []
        if msg.link_type == "REPLY":
            lines.extend(self._format_linked_block(msg))
        if msg.text:
            lines.append(_esc(msg.text))
        lines.extend(self._format_attachment_lines(msg.attachments))
        if msg.link_type != "REPLY":
            lines.extend(self._format_linked_block(msg))
        return lines

    def _format_linked_block(self, msg: UnifiedMessage) -> list[str]:
        """Render linked/quoted/forwarded content."""
        if not msg.link_type:
            return []

        lines = [self._link_label(msg.link_type)]
        linked = msg.linked_message
        if linked is not None and linked.text:
            lines.append(_esc(linked.text))
        if linked is not None:
            lines.extend(self._format_attachment_lines(linked.attachments))
        if len(lines) == 1:
            lines.append("<i>[без содержимого]</i>")
        return lines

    def _format_attachment_lines(self,
                                 attachments: list[dict[str, object]]) -> list[str]:
        """Render normalized attachments as readable text lines."""
        lines = []
        for attachment in attachments:
            atype = str(attachment.get("type", "?"))
            data = attachment.get("data", {})
            if not isinstance(data, dict):
                data = {}
            if atype == "AUDIO":
                dur = data.get("duration", 0)
                secs = dur // 1000 if isinstance(dur, int) and dur > 100 else dur
                lines.append(f"🎤 Голосовое ({secs}с)")
            elif atype == "VIDEO":
                dur = data.get("duration", 0)
                secs = dur // 1000 if isinstance(dur, int) and dur > 100 else dur
                lines.append(f"🎬 Видео ({secs}с)")
            elif atype == "PHOTO":
                lines.append("🖼 Фото")
            elif atype == "SHARE":
                title = data.get("title") or data.get("host") or data.get("url") or "Ссылка"
                lines.append(f"🔗 {_esc(str(title))}")
            else:
                name = data.get("fileName") or data.get("name") or data.get("title") or atype
                lines.append(f"📎 {_esc(str(name))}")
        return lines

    @staticmethod
    def _link_label(link_type: str) -> str:
        """Humanize linked message type."""
        if link_type == "FORWARD":
            return "↪ <b>Переслано</b>"
        if link_type == "REPLY":
            return "↩ <b>Ответ на сообщение</b>"
        return f"🔗 <b>{_esc(link_type)}</b>"

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
        if caption:
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

    async def _send_text(self, text: str) -> bool:
        if not self._config or not self._http:
            return False
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
                    return False
            return True
        except Exception:
            logger.debug("Ошибка Telegram sendMessage", exc_info=True)
            return False


def _esc(text: str) -> str:
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))


def _humanize_control_event(event: str) -> str:
    known = {
        "new": "создание чата",
        "join": "вступление",
        "add": "добавление участника",
        "invite": "приглашение участника",
        "remove": "удаление участника",
        "leave": "выход из чата",
        "call": "звонок",
        "call_start": "звонок",
        "video_chat_start": "звонок",
        "pin": "закрепление сообщения",
        "pin_message": "закрепление сообщения",
        "unpin": "открепление сообщения",
        "title_change": "смена названия",
        "description_change": "смена описания",
        "icon_change": "смена аватара",
    }
    normalized = event.strip().lower()
    if normalized in known:
        return known[normalized]
    return normalized.replace("-", " ").replace("_", " ")
