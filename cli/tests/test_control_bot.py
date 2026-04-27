"""Tests for the Telegram control bot."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

import maxbridge.telegram.control_bot as control_bot_module
from maxbridge.ipc.stats import StatsCollector
from maxbridge.telegram.config import TelegramConfig
from maxbridge.telegram.control_bot import (
    TelegramControlBot,
    _normalize_secret_text,
    _NormalizedSecret,
    _PendingSecretPrompt,
)


class TestTelegramControlBot:
    def test_normalize_secret_text_trims_and_removes_zero_width(self):
        normalized = _normalize_secret_text("  se\u200bcret  ")

        assert normalized == _NormalizedSecret(
            value="secret",
            raw_length=11,
            clean_length=6,
            trimmed=True,
            zero_width_removed=1,
        )

    def test_normalize_secret_text_rejects_hidden_control_chars(self):
        with pytest.raises(ValueError, match="скрытые символы"):
            _normalize_secret_text("secret\nline")

    def _make_bot(self):
        manager = MagicMock()
        manager.account_ids = ["default"]
        manager.status.return_value = {
            "default": {
                "connected": False,
                "phone": "+79990000000",
                "has_session": True,
            }
        }
        stats = StatsCollector()
        stats.record_message_received("default")
        stats.record_message_delivered("default")
        stats.record_rpc_error("send_message", "default", "temporary failure")

        bot = TelegramControlBot(manager, stats)
        bot._config = TelegramConfig(
            enabled=True,
            bot_token="123:ABC",
            chat_id="-100123",
            allowed_chat_ids=["-100123", "145185443"],
        )
        bot.set_status_provider(lambda: {
            "daemon": {"running": True, "shutdown_requested": False},
            "telegram": {
                "control_bot_ready": True,
                "forwarder_ready": True,
                "alerts_enabled": True,
            },
            "bridge": {
                "ipc_running": True,
                "ipc_clients": 2,
                "subscribers": 3,
            },
        })
        return bot, manager

    @pytest.mark.asyncio
    async def test_auth_command_starts_qr_flow(self):
        bot, _manager = self._make_bot()
        bot._send_message = AsyncMock()
        bot._start_auth_task = MagicMock(return_value=True)

        await bot._handle_message({
            "chat": {"id": -100123},
            "text": "/auth@msgMaxBridge_bot",
        })

        bot._start_auth_task.assert_called_once_with(
            "default",
            "Manual Telegram /auth request",
            requested_by="manual",
            target_chat_id="-100123",
        )
        bot._send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auth_command_ignores_foreign_chat(self):
        bot, _manager = self._make_bot()
        bot._send_message = AsyncMock()
        bot._start_auth_task = MagicMock(return_value=True)

        await bot._handle_message({
            "chat": {"id": 777},
            "text": "/auth",
        })

        bot._start_auth_task.assert_not_called()
        bot._send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_command_replies_to_private_chat(self):
        bot, _manager = self._make_bot()
        bot._send_message = AsyncMock()

        await bot._handle_message({
            "chat": {"id": 145185443},
            "text": "/status",
        })

        bot._send_message.assert_awaited_once()
        assert bot._send_message.await_args.kwargs["chat_id"] == "145185443"

    @pytest.mark.asyncio
    async def test_update_command_sends_runtime_summary(self):
        bot, _manager = self._make_bot()
        bot._send_message = AsyncMock()

        await bot._handle_message({
            "chat": {"id": -100123},
            "text": "/update",
        })

        bot._send_message.assert_awaited_once()
        text = bot._send_message.await_args.args[0]
        assert "Сводка maxBridge" in text
        assert "default" in text
        assert "temporary failure" in text

    @pytest.mark.asyncio
    async def test_status_command_sends_health_summary(self):
        bot, _manager = self._make_bot()
        bot._send_message = AsyncMock()

        await bot._handle_message({
            "chat": {"id": -100123},
            "text": "/status@msgMaxBridge_bot",
        })

        bot._send_message.assert_awaited_once()
        text = bot._send_message.await_args.args[0]
        assert "maxBridge не подключён к MAX" in text
        assert "🧭 Общая картина" in text
        assert "🤖 Telegram" in text
        assert "Бот управления: ✅ работает" in text
        assert "Форвардер: ✅ работает" in text
        assert "IPC-сервер: ✅ работает" in text
        assert "📎 Вложения" in text
        assert "RPC-ошибок: 1" in text
        assert "📡 RPC" in text

    @pytest.mark.asyncio
    async def test_request_auth_nowait_schedules_auto_flow(self):
        bot, _manager = self._make_bot()
        bot._loop = MagicMock()
        bot._loop.is_closed.return_value = False
        bot._loop.call_soon_threadsafe = lambda fn: fn()
        bot._http = MagicMock()
        bot._start_auth_task = MagicMock(return_value=True)

        bot.request_auth_nowait("default", RuntimeError("token expired"))

        bot._start_auth_task.assert_called_once_with(
            "default",
            "token expired",
            requested_by="auto",
        )

    @pytest.mark.asyncio
    async def test_non_command_message_fulfills_pending_secret_prompt(self):
        bot, _manager = self._make_bot()
        future = asyncio.get_running_loop().create_future()
        bot._pending_secret_prompts["145185443"] = _PendingSecretPrompt(
            future=future,
            account_id="default",
            prompt_message_id=11,
            kind="max-password",
        )

        await bot._handle_message({
            "chat": {"id": 145185443},
            "message_id": 77,
            "text": "secret-password",
        })

        assert future.done() is True
        reply = future.result()
        assert reply.text == "secret-password"
        assert reply.message_id == 77

    @pytest.mark.asyncio
    async def test_send_qr_message_prefers_photo(self, monkeypatch):
        bot, _manager = self._make_bot()
        bot._send_photo = AsyncMock(return_value={"result": {"message_id": 5}})
        bot._send_message = AsyncMock()
        monkeypatch.setattr(control_bot_module, "render_qr_png", lambda _url: b"png")

        response = await bot._send_qr_message(
            "default",
            "https://example.com/qr",
            "Manual Telegram /auth request",
            chat_id="145185443",
        )

        assert response == {"result": {"message_id": 5}}
        bot._send_photo.assert_awaited_once()
        bot._send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_send_qr_message_falls_back_to_text(self, monkeypatch):
        bot, _manager = self._make_bot()
        bot._send_photo = AsyncMock(return_value=None)
        bot._send_message = AsyncMock(return_value={"result": {"message_id": 6}})
        monkeypatch.setattr(control_bot_module, "render_qr_png", lambda _url: b"png")

        response = await bot._send_qr_message(
            "default",
            "https://example.com/qr",
            "Manual Telegram /auth request",
            chat_id="145185443",
        )

        assert response == {"result": {"message_id": 6}}
        bot._send_photo.assert_awaited_once()
        bot._send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_call_logs_transient_disconnect_as_warning(self, caplog):
        bot, _manager = self._make_bot()
        bot._http = _FailingHttp(
            aiohttp.ServerDisconnectedError("Server disconnected"),
        )

        with caplog.at_level(logging.WARNING, logger="maxbridge.telegram.control_bot"):
            result = await bot._call("getUpdates", {"timeout": 30})

        assert result is None
        assert "transient request error" in caplog.text
        assert "ServerDisconnectedError" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class _FailingHttp:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def post(self, *_args, **_kwargs):
        return _FailingContext(self._exc)


class _FailingContext:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *_args):
        return False
