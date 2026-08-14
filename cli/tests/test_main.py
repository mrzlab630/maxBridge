"""Tests for maxBridge process shutdown helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import maxbridge.main as main_module
from maxbridge.bridge.event_bus import EventBus
from maxbridge.config import ResolvedConfig, load_config
from maxbridge.main import (
    MaxBridgeDaemon,
    _drain_background_tasks,
    _register_configured_accounts,
)
from maxbridge.telegram import forwarder as forwarder_module
from maxbridge.telegram.config import TelegramConfig, save_telegram_config
from maxbridge.telegram.forwarder import TelegramForwarder
from maxbridge.utils.types import MessageStatus, UnifiedMessage


class _FakeClientSession:
    def __init__(self, **_kwargs):
        self.closed = False

    async def close(self):
        self.closed = True


def _message() -> UnifiedMessage:
    return UnifiedMessage(
        account_id="default",
        chat_id=123,
        message_id="m1",
        status=MessageStatus.NEW,
        text="runtime forwarding",
        sender_id=1,
        sender_name="Sender",
        chat_name="Chat",
        attachments=[],
    )


async def _reload_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(forwarder_module.aiohttp, "ClientSession", _FakeClientSession)
    path = tmp_path / "telegram.json"
    save_telegram_config(TelegramConfig(enabled=False), path)
    bus = EventBus()
    manager = MagicMock()
    manager.account_ids = ["default"]
    forwarder = TelegramForwarder(bus, manager, path)
    await forwarder.start()
    daemon = MaxBridgeDaemon.__new__(MaxBridgeDaemon)
    daemon._telegram_config_path = path
    daemon._telegram = forwarder
    daemon._control_bot = MagicMock()
    daemon._sync_telegram_error_handler = MagicMock()
    return daemon, forwarder, bus, path


class TestMainShutdown:
    @pytest.mark.asyncio
    async def test_drain_background_tasks_cancels_pending_tasks(self):
        loop = asyncio.get_running_loop()
        cancelled = asyncio.Event()

        async def background() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(background())
        await asyncio.sleep(0)

        await _drain_background_tasks(loop, shutdown_executor=False)

        assert cancelled.is_set()
        assert task.cancelled()


class TestTelegramConfigReload:
    @pytest.mark.asyncio
    async def test_running_daemon_enables_forwarder_and_forwards_without_restart(
        self, tmp_path, monkeypatch
    ):
        daemon, forwarder, bus, path = await _reload_daemon(tmp_path, monkeypatch)
        forwarder._send_text = AsyncMock(return_value=True)

        save_telegram_config(
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            path,
        )
        changed = await MaxBridgeDaemon._reload_telegram_config_if_changed(daemon)
        await bus.publish(_message())

        assert changed is True
        assert forwarder.is_ready
        assert bus.subscriber_count == 1
        forwarder._send_text.assert_awaited_once()
        daemon._sync_telegram_error_handler.assert_called_once_with()
        assert daemon._control_bot.mock_calls == []
        await forwarder.stop()

    @pytest.mark.asyncio
    async def test_running_daemon_disables_forwarder_and_stops_later_delivery(
        self, tmp_path, monkeypatch
    ):
        daemon, forwarder, bus, path = await _reload_daemon(tmp_path, monkeypatch)
        forwarder._send_text = AsyncMock(return_value=True)
        save_telegram_config(
            TelegramConfig(enabled=True, bot_token="token", chat_id="chat"),
            path,
        )
        await MaxBridgeDaemon._reload_telegram_config_if_changed(daemon)
        session = forwarder._http

        save_telegram_config(TelegramConfig(enabled=False), path)
        changed = await MaxBridgeDaemon._reload_telegram_config_if_changed(daemon)
        await bus.publish(_message())

        assert changed is True
        assert not forwarder.is_ready
        assert bus.subscriber_count == 0
        assert session is not None and session.closed
        forwarder._send_text.assert_not_awaited()
        assert daemon._sync_telegram_error_handler.call_count == 2
        await forwarder.stop()

    @pytest.mark.asyncio
    async def test_watcher_start_is_idempotent_and_stop_cancels_one_task(self):
        daemon = MaxBridgeDaemon.__new__(MaxBridgeDaemon)
        daemon._telegram_watch_task = None
        waiting = asyncio.Event()

        async def watch():
            await waiting.wait()

        daemon._watch_telegram_config = watch

        MaxBridgeDaemon._start_telegram_config_watch(daemon)
        task = daemon._telegram_watch_task
        MaxBridgeDaemon._start_telegram_config_watch(daemon)

        assert task is not None
        assert daemon._telegram_watch_task is task
        await MaxBridgeDaemon._stop_telegram_config_watch(daemon)
        assert task.cancelled()
        assert daemon._telegram_watch_task is None


def test_cli_passes_resolved_checkout_paths_to_daemon(tmp_path, monkeypatch):
    checkout = tmp_path / "cli"
    config_path = checkout / "config" / "local.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("{}\n", encoding="utf-8")
    daemon = MagicMock()
    daemon.start = AsyncMock()
    daemon.stop = AsyncMock()
    daemon.request_shutdown = MagicMock()
    daemon_cls = MagicMock(return_value=daemon)
    monkeypatch.setattr(main_module, "MaxBridgeDaemon", daemon_cls)
    monkeypatch.setattr(main_module, "setup_logging", MagicMock())
    monkeypatch.setattr(main_module, "_drain_background_tasks", AsyncMock())
    monkeypatch.setattr("sys.argv", ["maxbridge", "-c", str(config_path)])
    monkeypatch.setattr(main_module.os, "chdir", MagicMock())

    main_module.cli_entry()

    resolved = daemon_cls.call_args.args[0]
    assert resolved == load_config(config_path)
    assert resolved.source_path == config_path
    assert resolved.runtime_root == checkout
    assert resolved.telegram_config_path == checkout / "data" / "telegram.json"
    main_module.os.chdir.assert_called_once_with(checkout)


def test_register_accounts_loads_dynamic_store_with_config_priority(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "accounts.json").write_text(
        '{"default": {"session_file": "data/dynamic-default.session"}, '
        '"account_2": {"session_file": "data/account_2.session"}}',
        encoding="utf-8",
    )
    config = ResolvedConfig(
        {"accounts": {"default": {"session_file": "data/default.session"}}},
        source_path=None,
        runtime_root=tmp_path,
    )
    manager = MagicMock()

    _register_configured_accounts(manager, config)

    assert manager.add_account.call_args_list == [
        (("default", {"session_file": "data/default.session"}),),
        (("account_2", {"session_file": "data/account_2.session"}),),
    ]
