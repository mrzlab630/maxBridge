"""Тесты Telegram forwarder — форматирование и _esc."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.telegram import forwarder as forwarder_module
from maxbridge.telegram.forwarder import TelegramForwarder, TelegramLogHandler, _esc
from maxbridge.utils.types import LinkedMessage, MessageStatus, UnifiedMessage


def _msg(text="hi", sender_name="Иван", chat_name="Общий", message_id="m1",
         status=MessageStatus.NEW, attachments=None,
         link_type=None, link_chat_id=None, linked_message=None):
    return UnifiedMessage(
        account_id="default",
        chat_id=123,
        message_id=message_id,
        status=status,
        text=text,
        sender_id=1,
        sender_name=sender_name,
        chat_name=chat_name,
        attachments=attachments or [],
        link_type=link_type,
        link_chat_id=link_chat_id,
        linked_message=linked_message,
    )


class TestEscapeHtml:
    def test_basic(self):
        assert _esc("hello") == "hello"

    def test_angle_brackets(self):
        assert _esc("<b>bold</b>") == "&lt;b&gt;bold&lt;/b&gt;"

    def test_ampersand(self):
        assert _esc("a & b") == "a &amp; b"

    def test_all(self):
        assert _esc("<a&b>") == "&lt;a&amp;b&gt;"


class TestFormatMessage:
    def _make_forwarder(self):
        """Создаём forwarder без реальных зависимостей для тестирования формата."""
        bus = MagicMock()
        bus.has_subscriber.return_value = False
        manager = MagicMock()
        manager.account_ids = ["default"]
        fw = TelegramForwarder(bus, manager)
        return fw

    def test_format_header(self):
        fw = self._make_forwarder()
        msg = _msg()
        header = fw._format_header(msg)
        assert "<b>Иван</b>" in header
        assert "<b>Общий</b>" in header

    def test_format_edited(self):
        fw = self._make_forwarder()
        msg = _msg(status=MessageStatus.EDITED)
        header = fw._format_header(msg)
        assert "[ред.]" in header

    def test_format_full_with_text(self):
        fw = self._make_forwarder()
        msg = _msg(text="Привет мир")
        full = fw._format_full(msg)
        assert "Привет мир" in full
        assert "<b>Иван</b>" in full

    def test_format_full_with_audio(self):
        fw = self._make_forwarder()
        msg = _msg(text="", attachments=[
            {"type": "AUDIO", "data": {"duration": 3000}}
        ])
        full = fw._format_full(msg)
        assert "🎤" in full
        assert "3с" in full

    def test_format_full_with_video(self):
        fw = self._make_forwarder()
        msg = _msg(text="", attachments=[
            {"type": "VIDEO", "data": {"duration": 5000}}
        ])
        full = fw._format_full(msg)
        assert "🎬" in full
        assert "5с" in full

    def test_format_full_with_file(self):
        fw = self._make_forwarder()
        msg = _msg(text="", attachments=[
            {"type": "FILE", "data": {"fileName": "doc.pdf"}}
        ])
        full = fw._format_full(msg)
        assert "📎" in full
        assert "doc.pdf" in full

    def test_format_full_with_control_action(self):
        fw = self._make_forwarder()
        msg = _msg(text="", sender_name="Ирина", chat_name="Настольный теннис", attachments=[
            {"type": "CONTROL", "data": {"event": "call_start"}}
        ])
        full = fw._format_full(msg)
        assert "Ирина" in full
        assert "Настольный теннис" in full
        assert "совершил(а) действие" in full
        assert "звонок" in full
        assert "CONTROL" not in full

    def test_format_full_with_unknown_control_action(self):
        fw = self._make_forwarder()
        msg = _msg(text="", attachments=[
            {"type": "CONTROL", "data": {"event": "member_joined"}}
        ])
        full = fw._format_full(msg)
        assert "совершил(а) действие" in full
        assert "member joined" in full

    def test_format_full_with_forwarded_message(self):
        fw = self._make_forwarder()
        msg = _msg(
            text="",
            link_type="FORWARD",
            link_chat_id=-42,
            linked_message=LinkedMessage(
                text="Пересланный текст",
                attachments=[
                    {"type": "SHARE", "data": {"title": "Субботник 29.04"}},
                ],
            ),
        )
        full = fw._format_full(msg)
        assert "Переслано" in full
        assert "Пересланный текст" in full
        assert "Субботник 29.04" in full

    def test_format_full_with_reply_message(self):
        fw = self._make_forwarder()
        msg = _msg(
            text="Можно его тоже)",
            link_type="REPLY",
            linked_message=LinkedMessage(text="Один хватит"),
        )
        full = fw._format_full(msg)
        assert "Ответ на сообщение" in full
        assert "Один хватит" in full
        assert "Можно его тоже)" in full

    def test_extract_url_photo(self):
        url = TelegramForwarder._extract_url(
            "PHOTO", {"baseUrl": "https://example.com/photo"})
        assert url == "https://example.com/photo"

    def test_extract_url_video_prefers_real_media_url(self):
        url = TelegramForwarder._extract_url(
            "VIDEO",
            {
                "url": "https://example.com/video.mp4",
                "thumbnail": "https://example.com/thumb.jpg",
            },
        )
        assert url == "https://example.com/video.mp4"

    def test_extract_url_video_ignores_thumbnail_preview(self):
        url = TelegramForwarder._extract_url(
            "VIDEO", {"thumbnail": "https://example.com/thumb.jpg"})
        assert url == ""

    def test_extract_url_audio_returns_empty(self):
        """AUDIO url привязан к IP — не возвращаем."""
        url = TelegramForwarder._extract_url(
            "AUDIO", {"url": "https://cdn.example.com/audio"})
        assert url == ""

    def test_find_media_photo(self):
        fw = self._make_forwarder()
        msg = _msg(attachments=[
            {"type": "PHOTO", "data": {"baseUrl": "https://x.com/p"}}
        ])
        media = fw._find_media(msg)
        assert media is not None
        assert media["type"] == "PHOTO"
        assert media["url"] == "https://x.com/p"

    def test_find_media_none(self):
        fw = self._make_forwarder()
        msg = _msg(attachments=[
            {"type": "AUDIO", "data": {"url": "https://x.com/a"}}
        ])
        media = fw._find_media(msg)
        assert media is None

    def test_find_media_video_with_file_id(self):
        fw = self._make_forwarder()
        msg = _msg(attachments=[
            {"type": "VIDEO", "data": {"fileId": 42, "thumbnail": "https://x.com/thumb"}}
        ])

        media = fw._find_media(msg)

        assert media is not None
        assert media["type"] == "VIDEO"
        assert media["url"] == ""
        assert media["file_id"] == 42

    def test_find_media_from_forwarded_share_preview(self):
        fw = self._make_forwarder()
        msg = _msg(
            text="",
            link_type="FORWARD",
            linked_message=LinkedMessage(
                attachments=[
                    {
                        "type": "SHARE",
                        "data": {
                            "image": {"_type": "PHOTO", "url": "https://x.com/share-preview"},
                        },
                    },
                ],
            ),
        )
        media = fw._find_media(msg)
        assert media is not None
        assert media["type"] == "PHOTO"
        assert media["url"] == "https://x.com/share-preview"

    def test_format_alert(self):
        alert = TelegramForwarder._format_alert(
            "Ошибка <MAX>", "token expired & session invalid"
        )
        assert "<b>Ошибка &lt;MAX&gt;</b>" in alert
        assert "<pre>token expired &amp; session invalid</pre>" in alert


class TestErrorNotifications:
    def _make_forwarder(self):
        bus = MagicMock()
        bus.has_subscriber.return_value = False
        manager = MagicMock()
        manager.account_ids = ["default"]
        return TelegramForwarder(bus, manager)

    def test_log_handler_forwards_error_record(self):
        fw = self._make_forwarder()
        fw.send_alert_nowait = MagicMock()
        handler = TelegramLogHandler(fw)
        record = logging.LogRecord(
            name="maxbridge.client.connection",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="token expired",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        fw.send_alert_nowait.assert_called_once_with("token expired")

    def test_log_handler_ignores_forwarder_logs(self):
        fw = self._make_forwarder()
        fw.send_alert_nowait = MagicMock()
        handler = TelegramLogHandler(fw)
        record = logging.LogRecord(
            name="maxbridge.telegram.forwarder",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="telegram send failed",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        fw.send_alert_nowait.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_media_video_uses_send_video_not_photo(self):
        fw = self._make_forwarder()
        fw._download = AsyncMock(return_value=b"video-bytes")
        fw._tg_send_file = AsyncMock(return_value=True)
        msg = _msg(text="clip", attachments=[
            {"type": "VIDEO", "data": {"url": "https://x.com/video.mp4"}}
        ])

        result = await fw._send_media(
            {"type": "VIDEO", "url": "https://x.com/video.mp4", "data": {}},
            msg,
        )

        assert result is True
        fw._download.assert_awaited_once_with("https://x.com/video.mp4")
        fw._tg_send_file.assert_awaited_once()
        args = fw._tg_send_file.await_args.args
        assert args[:4] == ("sendVideo", "video", b"video-bytes", "video.mp4")

    @pytest.mark.asyncio
    async def test_send_media_video_resolves_download_url_by_file_id(self, monkeypatch):
        fw = self._make_forwarder()
        account = MagicMock()
        account.is_connected = True
        account.connection = MagicMock()
        fw._manager.get.return_value = account
        get_download_url = AsyncMock(return_value="https://x.com/download/video.mp4")
        monkeypatch.setattr(forwarder_module, "get_download_url", get_download_url)
        fw._download = AsyncMock(return_value=b"video-bytes")
        fw._tg_send_file = AsyncMock(return_value=True)
        msg = _msg(message_id="m-video")

        result = await fw._send_media(
            {"type": "VIDEO", "url": "", "file_id": 42, "data": {"fileName": "clip"}},
            msg,
        )

        assert result is True
        get_download_url.assert_awaited_once_with(
            account.connection, 123, "m-video", 42, "video",
        )
        fw._download.assert_awaited_once_with("https://x.com/download/video.mp4")
        args = fw._tg_send_file.await_args.args
        assert args[:4] == ("sendVideo", "video", b"video-bytes", "clip.mp4")

    @pytest.mark.asyncio
    async def test_send_alert_uses_send_text(self):
        fw = self._make_forwarder()
        fw._config = MagicMock(enabled=True, bot_token="token", chat_id="chat")
        fw._http = MagicMock()
        fw._send_text = AsyncMock(return_value=True)

        result = await fw.send_alert("fatal startup error")

        assert result is True
        fw._send_text.assert_called_once()
        assert "fatal startup error" in fw._send_text.call_args[0][0]
