"""Tests for delayed attachment recovery handler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.handlers.attachment import create_upload_complete_handler
from maxbridge.ipc.stats import StatsCollector


def _make_packet(payload):
    return {"opcode": 136, "payload": payload}


class TestUploadCompleteHandler:
    @pytest.mark.asyncio
    async def test_direct_packet_with_message_is_published(self):
        event_bus = MagicMock()
        event_bus.subscriber_count = 1
        event_bus.publish = AsyncMock()
        connection = MagicMock()
        connection.is_connected = False
        stats = StatsCollector()

        handler = create_upload_complete_handler(
            event_bus,
            connection=connection,
            account_id="default",
            stats=stats,
        )

        await handler(
            AsyncMock(),
            _make_packet({
                "chatId": 123,
                "message": {
                    "id": "m1",
                    "text": "hello",
                    "sender": {"userId": 7},
                    "attaches": [{"_type": "PHOTO", "baseUrl": "https://x/p.jpg"}],
                },
            }),
        )

        event_bus.publish.assert_awaited_once()
        published = event_bus.publish.await_args.args[0]
        assert published.chat_id == 123
        assert published.message_id == "m1"
        assert published.attachments[0]["type"] == "PHOTO"
        stats_snapshot = stats.get_stats()
        assert stats_snapshot["attachments"]["total_notifications"] == 1
        assert stats_snapshot["attachments"]["total_reconciled"] == 1
        assert stats_snapshot["attachments"]["total_unresolved"] == 0

    @pytest.mark.asyncio
    async def test_refetches_history_when_only_ids_are_available(self):
        event_bus = MagicMock()
        event_bus.subscriber_count = 1
        event_bus.publish = AsyncMock()
        connection = MagicMock()
        connection.is_connected = False
        connection.get_chat_history = AsyncMock(return_value={
            "payload": {
                "messages": [
                    {
                        "id": "m2",
                        "text": "photo",
                        "sender": 9,
                        "attaches": [{"_type": "PHOTO", "baseUrl": "https://x/2.jpg"}],
                    }
                ]
            }
        })
        stats = StatsCollector()

        handler = create_upload_complete_handler(
            event_bus,
            connection=connection,
            account_id="default",
            stats=stats,
        )

        await handler(
            AsyncMock(),
            _make_packet({
                "chatId": 123,
                "messageId": "m2",
                "attach": {"_type": "PHOTO"},
            }),
        )

        connection.get_chat_history.assert_awaited_once_with(123, count=30)
        event_bus.publish.assert_awaited_once()
        published = event_bus.publish.await_args.args[0]
        assert published.message_id == "m2"
        assert published.text == "photo"
        assert stats.get_stats()["attachments"]["total_reconciled"] == 1

    @pytest.mark.asyncio
    async def test_missing_message_id_is_buffered_as_unresolved(self):
        event_bus = MagicMock()
        event_bus.subscriber_count = 1
        event_bus.publish = AsyncMock()
        connection = MagicMock()
        connection.is_connected = False
        stats = StatsCollector()

        handler = create_upload_complete_handler(
            event_bus,
            connection=connection,
            account_id="default",
            stats=stats,
        )

        await handler(
            AsyncMock(),
            _make_packet({
                "chatId": 123,
                "attach": {"_type": "PHOTO"},
            }),
        )

        event_bus.publish.assert_not_called()
        errors = stats.get_errors(limit=1)
        assert stats.get_stats()["attachments"]["total_unresolved"] == 1
        assert errors[0]["kind"] == "attachment"
        assert "missing messageId" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_history_miss_is_buffered_as_unresolved(self):
        event_bus = MagicMock()
        event_bus.subscriber_count = 1
        event_bus.publish = AsyncMock()
        connection = MagicMock()
        connection.is_connected = False
        connection.get_chat_history = AsyncMock(return_value={"payload": {"messages": []}})
        stats = StatsCollector()

        handler = create_upload_complete_handler(
            event_bus,
            connection=connection,
            account_id="default",
            stats=stats,
        )

        await handler(
            AsyncMock(),
            _make_packet({
                "chatId": 123,
                "messageId": "m404",
            }),
        )

        event_bus.publish.assert_not_called()
        assert connection.get_chat_history.await_count == 2
        assert stats.get_stats()["attachments"]["total_unresolved"] == 1

    @pytest.mark.asyncio
    async def test_history_error_is_buffered_as_unresolved(self):
        event_bus = MagicMock()
        event_bus.subscriber_count = 1
        event_bus.publish = AsyncMock()
        connection = MagicMock()
        connection.is_connected = False
        connection.get_chat_history = AsyncMock(side_effect=RuntimeError("ws timeout"))
        stats = StatsCollector()

        handler = create_upload_complete_handler(
            event_bus,
            connection=connection,
            account_id="default",
            stats=stats,
        )

        await handler(
            AsyncMock(),
            _make_packet({
                "chatId": 123,
                "messageId": "m500",
            }),
        )

        event_bus.publish.assert_not_called()
        errors = stats.get_errors(limit=1)
        assert stats.get_stats()["attachments"]["total_unresolved"] == 1
        assert "history error" in errors[0]["error"]
