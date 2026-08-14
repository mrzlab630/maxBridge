"""Тесты Telegram forwarder — форматирование и _esc."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.bridge.event_bus import EventBus
from maxbridge.telegram import forwarder as forwarder_module
from maxbridge.telegram.config import (
    TelegramConfig,
    load_telegram_config_snapshot,
    save_telegram_config,
)
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


class _FakeClientSession:
    instances = []

    def __init__(self, **_kwargs):
        self.closed = False
        self.close_count = 0
        self.instances.append(self)

    async def close(self):
        self.closed = True
        self.close_count += 1


def _forwarder(tmp_path, monkeypatch, config: TelegramConfig):
    _FakeClientSession.instances = []
    monkeypatch.setattr(forwarder_module.aiohttp, "ClientSession", _FakeClientSession)
    path = tmp_path / "telegram.json"
    save_telegram_config(config, path)
    bus = EventBus()
    manager = MagicMock()
    manager.account_ids = ["default"]
    return TelegramForwarder(bus, manager, path), bus, path


class TestLiveReload:
    @pytest.mark.asyncio
    async def test_repeated_valid_reload_keeps_one_subscriber_and_session(
        self, tmp_path, monkeypatch
    ):
        fw, bus, path = _forwarder(
            tmp_path,
            monkeypatch,
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
        )
        await fw.start()
        session = _FakeClientSession.instances[0]

        changed = await fw.reload(load_telegram_config_snapshot(path))

        assert changed is False
        assert fw.is_ready
        assert bus.subscriber_count == 1
        assert _FakeClientSession.instances == [session]
        assert session.close_count == 0
        await fw.stop()

    @pytest.mark.asyncio
    async def test_changed_ready_config_reuses_one_session_and_subscriber(
        self, tmp_path, monkeypatch
    ):
        fw, bus, path = _forwarder(
            tmp_path,
            monkeypatch,
            TelegramConfig(enabled=True, bot_token="old-token", chat_id="old-chat"),
        )
        await fw.start()
        session = _FakeClientSession.instances[0]

        save_telegram_config(
            TelegramConfig(enabled=True, bot_token="new-token", chat_id="new-chat"),
            path,
        )
        changed = await fw.reload(load_telegram_config_snapshot(path))

        assert changed is True
        assert fw.is_ready
        assert bus.subscriber_count == 1
        assert _FakeClientSession.instances == [session]
        assert session.close_count == 0
        assert fw._config is not None
        assert fw._config.bot_token == "new-token"
        assert fw._config.chat_id == "new-chat"
        await fw.stop()

    @pytest.mark.asyncio
    async def test_malformed_and_missing_config_keep_last_known_good_without_spinning(
        self, tmp_path, monkeypatch, caplog
    ):
        fw, bus, path = _forwarder(
            tmp_path,
            monkeypatch,
            TelegramConfig(enabled=True, bot_token="super-secret", chat_id="chat"),
        )
        await fw.start()
        session = _FakeClientSession.instances[0]
        caplog.set_level(logging.WARNING, logger="maxbridge.telegram.forwarder")

        path.write_text('{"bot_token": "super-secret",', encoding="utf-8")
        malformed = load_telegram_config_snapshot(path)
        assert await fw.reload(malformed) is False
        assert await fw.reload(malformed) is False
        path.unlink()
        missing = load_telegram_config_snapshot(path)
        assert await fw.reload(missing) is False
        assert await fw.reload(missing) is False

        assert fw.is_ready
        assert bus.subscriber_count == 1
        assert session.close_count == 0
        assert _FakeClientSession.instances == [session]
        assert caplog.text.count("keeping last-known-good state") == 2
        assert "super-secret" not in caplog.text
        await fw.stop()

    @pytest.mark.asyncio
    async def test_valid_disable_closes_session_and_unsubscribes(self, tmp_path, monkeypatch):
        fw, bus, path = _forwarder(
            tmp_path,
            monkeypatch,
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
        )
        await fw.start()
        session = _FakeClientSession.instances[0]
        fw._send_text = AsyncMock(return_value=True)

        save_telegram_config(TelegramConfig(enabled=False), path)
        assert await fw.reload(load_telegram_config_snapshot(path)) is True
        await bus.publish(_msg(text="must not forward"))

        assert not fw.is_ready
        assert bus.subscriber_count == 0
        assert session.closed
        assert session.close_count == 1
        fw._send_text.assert_not_awaited()
        await fw.stop()


class TestMessageCoalescing:
    def _make_ready_forwarder(self):
        bus = MagicMock()
        manager = MagicMock()
        manager.account_ids = ["default"]
        fw = TelegramForwarder(bus, manager)
        fw._config = TelegramConfig(enabled=True, bot_token="token", chat_id="chat")
        fw._http = MagicMock(closed=False)
        return fw

    @pytest.mark.asyncio
    async def test_concurrent_duplicate_uses_enriched_header_once(self, monkeypatch):
        fw = self._make_ready_forwarder()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0)
        fw._send_text = AsyncMock(return_value=True)
        raw = _msg(sender_name=None, chat_name=None)
        enriched = _msg(sender_name="Степан", chat_name="DM")

        await asyncio.gather(fw._on_message(raw), fw._on_message(enriched))

        fw._send_text.assert_awaited_once()
        text = fw._send_text.await_args.args[0]
        assert "Степан" in text
        assert "DM" in text

    @pytest.mark.asyncio
    async def test_distinct_message_ids_both_forward(self, monkeypatch):
        fw = self._make_ready_forwarder()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0)
        fw._send_text = AsyncMock(return_value=True)

        await asyncio.gather(
            fw._on_message(_msg(message_id="first")),
            fw._on_message(_msg(message_id="second")),
        )

        assert fw._send_text.await_count == 2

    @pytest.mark.asyncio
    async def test_same_id_with_different_text_both_forward(self, monkeypatch):
        fw = self._make_ready_forwarder()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0)
        fw._send_text = AsyncMock(return_value=True)

        await asyncio.gather(
            fw._on_message(_msg(text="before")),
            fw._on_message(_msg(text="after")),
        )

        assert fw._send_text.await_count == 2

    @pytest.mark.asyncio
    async def test_same_id_with_different_status_both_forward(self, monkeypatch):
        fw = self._make_ready_forwarder()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0)
        fw._send_text = AsyncMock(return_value=True)

        await asyncio.gather(
            fw._on_message(_msg(status=MessageStatus.NEW)),
            fw._on_message(_msg(status=MessageStatus.EDITED)),
        )

        assert fw._send_text.await_count == 2

    @pytest.mark.asyncio
    async def test_missing_message_id_keeps_existing_forwarding_behavior(self):
        fw = self._make_ready_forwarder()
        fw._send_text = AsyncMock(return_value=True)

        await asyncio.gather(
            fw._on_message(_msg(message_id="")),
            fw._on_message(_msg(message_id="")),
        )

        assert fw._send_text.await_count == 2

    @pytest.mark.asyncio
    async def test_delayed_attachment_content_is_not_coalesced(self, monkeypatch):
        fw = self._make_ready_forwarder()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0)
        fw._send_text = AsyncMock(return_value=True)
        fw._send_media_items = AsyncMock(return_value=(1, []))
        text_only = _msg(text="file incoming")
        with_attachment = _msg(
            text="file incoming",
            attachments=[{"type": "PHOTO", "data": {"baseUrl": "https://x.test/a.jpg"}}],
        )

        await asyncio.gather(
            fw._on_message(text_only),
            fw._on_message(with_attachment),
        )

        fw._send_media_items.assert_awaited_once()
        fw._send_text.assert_awaited_once()

    def test_coalescing_cache_expires_and_stays_bounded(self, monkeypatch):
        fw = self._make_ready_forwarder()
        clock = [100.0]
        monkeypatch.setattr(forwarder_module.time, "monotonic", lambda: clock[0])

        first = _msg(message_id="first")
        assert fw._claim_message(fw._message_identity(first), first)
        assert not fw._claim_message(fw._message_identity(first), first)
        for index in range(forwarder_module._COALESCE_MAX_ENTRIES + 1):
            msg = _msg(message_id=f"bounded-{index}")
            assert fw._claim_message(fw._message_identity(msg), msg)

        assert len(fw._coalescing_seen) == forwarder_module._COALESCE_MAX_ENTRIES
        clock[0] += forwarder_module._COALESCE_TTL + 1
        fw._prune_coalescing()
        assert fw._coalescing_seen == {}
        assert fw._coalescing_pending == {}

    @pytest.mark.asyncio
    async def test_stop_invalidates_waiting_coalesced_delivery(self, monkeypatch):
        fw = self._make_ready_forwarder()
        fw._bus.has_subscriber.return_value = False
        fw._http.close = AsyncMock()
        monkeypatch.setattr(forwarder_module, "_COALESCE_WINDOW", 0.05)
        fw._send_text = AsyncMock(return_value=True)

        delivery = asyncio.create_task(fw._on_message(_msg()))
        await asyncio.sleep(0)
        await fw.stop()
        await delivery

        fw._send_text.assert_not_awaited()


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

    def test_find_media_audio_direct_url(self):
        fw = self._make_forwarder()
        msg = _msg(attachments=[
            {"type": "AUDIO", "data": {"url": "https://x.com/a"}}
        ])
        media = fw._find_media(msg)
        assert media is not None
        assert media["type"] == "AUDIO"
        assert media["url"] == "https://x.com/a"

    def test_collect_media_returns_all_attachments(self):
        fw = self._make_forwarder()
        msg = _msg(attachments=[
            {"type": "PHOTO", "data": {"baseUrl": "https://x.com/1.jpg"}},
            {"type": "PHOTO", "data": {"baseUrl": "https://x.com/2.jpg"}},
            {"type": "FILE", "data": {"fileId": 77, "fileName": "doc.pdf"}},
        ])

        media_items = fw._collect_media(msg)

        assert [item["type"] for item in media_items] == ["PHOTO", "PHOTO", "FILE"]
        assert [item["url"] for item in media_items[:2]] == [
            "https://x.com/1.jpg", "https://x.com/2.jpg",
        ]
        assert media_items[2]["file_id"] == 77

    def test_format_media_caption_omits_attachment_placeholders(self):
        fw = self._make_forwarder()
        msg = _msg(text="files", attachments=[
            {"type": "PHOTO", "data": {"baseUrl": "https://x.com/p.jpg"}},
            {"type": "FILE", "data": {"fileId": 77, "fileName": "doc.pdf"}},
        ])

        caption = fw._format_media_caption(msg)

        assert "files" in caption
        assert "🖼 Фото" not in caption
        assert "doc.pdf" not in caption

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
    async def test_send_media_file_resolves_download_url_by_file_id(self, monkeypatch):
        fw = self._make_forwarder()
        account = MagicMock()
        account.is_connected = True
        account.connection = MagicMock()
        fw._manager.get.return_value = account
        get_download_url = AsyncMock(return_value="https://x.com/download/doc.pdf")
        monkeypatch.setattr(forwarder_module, "get_download_url", get_download_url)
        fw._download = AsyncMock(return_value=b"pdf-bytes")
        fw._tg_send_file = AsyncMock(return_value=True)
        msg = _msg(message_id="m-file")

        result = await fw._send_media(
            {"type": "FILE", "url": "", "file_id": 77, "data": {"fileName": "doc.pdf"}},
            msg,
        )

        assert result is True
        get_download_url.assert_awaited_once_with(
            account.connection, 123, "m-file", 77, "file",
        )
        fw._download.assert_awaited_once_with("https://x.com/download/doc.pdf")
        args = fw._tg_send_file.await_args.args
        assert args[:4] == ("sendDocument", "document", b"pdf-bytes", "doc.pdf")

    @pytest.mark.asyncio
    async def test_send_media_items_sends_all_with_caption_only_once(self):
        fw = self._make_forwarder()
        fw._send_media = AsyncMock(return_value=True)
        msg = _msg(text="bundle")
        media_items = [
            {"type": "PHOTO", "url": "https://x.com/1.jpg", "data": {}},
            {"type": "FILE", "url": "", "file_id": 77, "data": {"fileName": "doc.pdf"}},
        ]

        sent_count, failed = await fw._send_media_items(media_items, msg)

        assert sent_count == 2
        assert failed == []
        assert fw._send_media.await_count == 2
        first_caption = fw._send_media.await_args_list[0].kwargs["caption"]
        second_caption = fw._send_media.await_args_list[1].kwargs["caption"]
        assert "bundle" in first_caption
        assert second_caption == ""

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
