"""Тесты Telegram forwarder — форматирование и _esc."""

from maxbridge.telegram.forwarder import TelegramForwarder, _esc
from maxbridge.utils.types import MessageStatus, UnifiedMessage


def _msg(text="hi", sender_name="Иван", chat_name="Общий",
         status=MessageStatus.NEW, attachments=None):
    return UnifiedMessage(
        account_id="default",
        chat_id=123,
        message_id="m1",
        status=status,
        text=text,
        sender_id=1,
        sender_name=sender_name,
        chat_name=chat_name,
        attachments=attachments or [],
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
        from unittest.mock import MagicMock
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

    def test_extract_url_photo(self):
        url = TelegramForwarder._extract_url(
            "PHOTO", {"baseUrl": "https://example.com/photo"})
        assert url == "https://example.com/photo"

    def test_extract_url_video(self):
        url = TelegramForwarder._extract_url(
            "VIDEO", {"thumbnail": "https://example.com/thumb"})
        assert url == "https://example.com/thumb"

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
