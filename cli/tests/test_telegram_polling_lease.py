"""Telegram polling ownership and external-conflict tests."""

import asyncio
import hashlib
import logging
import multiprocessing
import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

import maxbridge.telegram.control_bot as control_bot_module
from maxbridge.ipc.stats import StatsCollector
from maxbridge.telegram.config import TelegramConfig
from maxbridge.telegram.control_bot import TelegramControlBot
from maxbridge.telegram.polling_lease import (
    TelegramPollingLease,
    TelegramPollingLeaseConflict,
    TelegramPollingLeaseUnavailable,
)


def _bot() -> TelegramControlBot:
    manager = MagicMock()
    manager.account_ids = ["default"]
    return TelegramControlBot(manager, StatsCollector())


class _FakeHttp:
    def __init__(self, events=None, close_error=None) -> None:
        self.closed = False
        self._events = events
        self._close_error = close_error

    async def close(self) -> None:
        self.closed = True
        if self._events is not None:
            self._events.append("http_close")
        if self._close_error is not None:
            raise self._close_error


def _hold_lease_in_child(token: str, connection) -> None:
    lease = TelegramPollingLease(token)
    lease.acquire()
    connection.send("bound")
    connection.recv()
    lease.release()
    connection.send("released")
    connection.close()


def test_same_token_contender_conflicts_and_release_allows_recovery():
    owner = TelegramPollingLease("test-token-owner")
    contender = TelegramPollingLease("test-token-owner")
    owner.acquire()

    try:
        with pytest.raises(TelegramPollingLeaseConflict, match="already active"):
            contender.acquire()
        assert owner.held is True
        assert contender.held is False
    finally:
        owner.release()

    contender.acquire()
    assert contender.held is True
    contender.release()
    contender.release()


def test_different_tokens_can_hold_leases_together():
    first = TelegramPollingLease("test-token-first")
    second = TelegramPollingLease("test-token-second")
    first.acquire()
    second.acquire()
    try:
        assert first.held is True
        assert second.held is True
    finally:
        second.release()
        first.release()


def test_process_contender_is_deterministic_without_sleep():
    token = "test-token-process-contender"
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe()
    process = context.Process(target=_hold_lease_in_child, args=(token, child))
    process.start()
    try:
        assert parent.recv() == "bound"

        contender = TelegramPollingLease(token)
        with pytest.raises(TelegramPollingLeaseConflict):
            contender.acquire()

        parent.send("release")
        assert parent.recv() == "released"
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            parent.send("release")
            process.join(timeout=5)

    contender.acquire()
    contender.release()
    parent.close()


def test_unavailable_socket_failure_is_sanitized(monkeypatch):
    token = "test-token-unavailable"
    digest = hashlib.sha256(token.encode()).hexdigest()[:32]

    def fail_socket(*_args, **_kwargs):
        raise OSError("platform detail must not escape")

    monkeypatch.setattr(socket, "socket", fail_socket)
    lease = TelegramPollingLease(token)

    with pytest.raises(TelegramPollingLeaseUnavailable) as exc_info:
        lease.acquire()

    rendered = repr(exc_info.value) + str(exc_info.value)
    assert token not in rendered
    assert digest not in rendered
    assert "platform detail" not in rendered


@pytest.mark.asyncio
async def test_start_acquires_lease_before_http_and_bootstrap(monkeypatch):
    bot = _bot()
    events = []
    fake_http = _FakeHttp(events)

    class FakeLease:
        held = False

        def __init__(self, _token):
            events.append("lease_init")

        def acquire(self):
            self.held = True
            events.append("lease_acquire")

        def release(self):
            self.held = False
            events.append("lease_release")

    def make_http(*_args, **_kwargs):
        events.append("http")
        return fake_http

    async def bootstrap():
        events.append("bootstrap")
        return 0

    async def poll_forever():
        await asyncio.Event().wait()

    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token="test-token-start-order",
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module, "TelegramPollingLease", FakeLease)
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", make_http)
    monkeypatch.setattr(bot, "_bootstrap_offset", bootstrap)
    monkeypatch.setattr(bot, "_poll_loop", poll_forever)

    await bot.start()
    await bot.start()
    assert events == ["lease_init", "lease_acquire", "http", "bootstrap"]
    await bot.stop()
    assert fake_http.closed is True
    assert events[-2:] == ["http_close", "lease_release"]


@pytest.mark.asyncio
async def test_local_loser_makes_zero_bootstrap_or_http_calls(monkeypatch):
    owner = TelegramPollingLease("test-token-local-loser")
    owner.acquire()
    bot = _bot()
    bootstrap = AsyncMock()
    http_factory = MagicMock()
    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token="test-token-local-loser",
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", http_factory)
    monkeypatch.setattr(bot, "_bootstrap_offset", bootstrap)

    try:
        with pytest.raises(TelegramPollingLeaseConflict):
            await bot.start()
    finally:
        owner.release()

    http_factory.assert_not_called()
    bootstrap.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_lease_makes_zero_http_or_bootstrap_calls(monkeypatch):
    bot = _bot()
    bootstrap = AsyncMock()
    http_factory = MagicMock()

    class UnavailableLease:
        def __init__(self, _token):
            pass

        def acquire(self):
            raise TelegramPollingLeaseUnavailable("polling lease unavailable")

    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token="test-token-unavailable-start",
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module, "TelegramPollingLease", UnavailableLease)
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", http_factory)
    monkeypatch.setattr(bot, "_bootstrap_offset", bootstrap)

    with pytest.raises(TelegramPollingLeaseUnavailable):
        await bot.start()

    http_factory.assert_not_called()
    bootstrap.assert_not_awaited()
    assert bot.polling_health["conflict_state"] == "stopped"


@pytest.mark.asyncio
async def test_post_acquisition_startup_failure_releases_lease(monkeypatch):
    token = "test-token-startup-unwind"
    bot = _bot()
    events = []
    fake_http = _FakeHttp(events)

    class TrackedLease(TelegramPollingLease):
        def release(self):
            events.append("lease_release")
            super().release()

    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token=token,
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module, "TelegramPollingLease", TrackedLease)
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", lambda **_kwargs: fake_http)
    monkeypatch.setattr(bot, "_bootstrap_offset", AsyncMock(side_effect=RuntimeError("failed")))

    with pytest.raises(RuntimeError, match="failed"):
        await bot.start()

    assert fake_http.closed is True
    assert events == ["http_close", "lease_release"]
    contender = TelegramPollingLease(token)
    contender.acquire()
    contender.release()


@pytest.mark.asyncio
async def test_http_close_failure_still_releases_lease(monkeypatch):
    token = "test-token-close-failure"
    bot = _bot()
    fake_http = _FakeHttp(close_error=RuntimeError("close failed"))
    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token=token,
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", lambda **_kwargs: fake_http)
    monkeypatch.setattr(bot, "_bootstrap_offset", AsyncMock(return_value=0))

    await bot.start()
    await bot.stop()

    contender = TelegramPollingLease(token)
    contender.acquire()
    contender.release()


@pytest.mark.asyncio
async def test_bootstrap_http_409_is_single_shot_degraded_and_latched(monkeypatch, caplog):
    bot = _bot()
    fake_http = _FakeHttp()
    get_updates = AsyncMock(side_effect=control_bot_module.TelegramPollingExternalConflict())
    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token="test-token-bootstrap-conflict",
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", lambda **_kwargs: fake_http)
    monkeypatch.setattr(bot, "_get_updates", get_updates)

    with caplog.at_level(logging.WARNING, logger="maxbridge.telegram.control_bot"):
        await bot.start()
        await bot.start()

    assert get_updates.await_count == 1
    assert bot.polling_health == {
        "polling_enabled": False,
        "lease_held": False,
        "conflict_state": "external_conflict",
        "consecutive_conflicts": 1,
    }
    assert fake_http.closed is True
    assert caplog.text.count("external polling conflict") == 1
    assert "test-token-bootstrap-conflict" not in caplog.text
    assert "test-chat" not in caplog.text


@pytest.mark.asyncio
async def test_fresh_object_can_start_after_external_conflict_release(monkeypatch):
    config = TelegramConfig(
        enabled=True,
        bot_token="test-token-operator-restart",
        chat_id="test-chat",
    )
    conflicted = _bot()
    fresh = _bot()
    first_http = _FakeHttp()
    second_http = _FakeHttp()
    sessions = iter([first_http, second_http])
    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: config)
    monkeypatch.setattr(
        control_bot_module.aiohttp,
        "ClientSession",
        lambda **_kwargs: next(sessions),
    )
    monkeypatch.setattr(
        conflicted,
        "_get_updates",
        AsyncMock(side_effect=control_bot_module.TelegramPollingExternalConflict()),
    )
    monkeypatch.setattr(fresh, "_bootstrap_offset", AsyncMock(return_value=0))

    await conflicted.start()
    await conflicted.start()
    await fresh.start()

    assert conflicted.polling_health["conflict_state"] == "external_conflict"
    assert fresh.polling_health == {
        "polling_enabled": True,
        "lease_held": True,
        "conflict_state": "healthy",
        "consecutive_conflicts": 0,
    }
    await fresh.stop()


@pytest.mark.asyncio
async def test_poll_loop_http_409_does_not_retry_and_releases_resources(monkeypatch):
    bot = _bot()
    fake_http = _FakeHttp()
    calls = 0

    async def get_updates(timeout):
        nonlocal calls
        calls += 1
        if timeout == 0:
            return []
        raise control_bot_module.TelegramPollingExternalConflict()

    monkeypatch.setattr(control_bot_module, "load_telegram_config", lambda: TelegramConfig(
        enabled=True,
        bot_token="test-token-poll-conflict",
        chat_id="test-chat",
    ))
    monkeypatch.setattr(control_bot_module.aiohttp, "ClientSession", lambda **_kwargs: fake_http)
    monkeypatch.setattr(bot, "_get_updates", get_updates)

    await bot.start()
    await asyncio.wait_for(bot._poll_task, timeout=1)

    assert calls == 2
    assert fake_http.closed is True
    assert bot.polling_health["conflict_state"] == "external_conflict"
    assert bot.polling_health["lease_held"] is False


def test_polling_health_exposes_only_bounded_fields():
    bot = _bot()

    assert set(bot.polling_health) == {
        "polling_enabled",
        "lease_held",
        "conflict_state",
        "consecutive_conflicts",
    }


@pytest.mark.asyncio
async def test_http_409_is_detected_without_reading_response_body():
    bot = _bot()
    response = MagicMock()
    response.status = 409
    response.json = AsyncMock()

    with pytest.raises(control_bot_module.TelegramPollingExternalConflict):
        await bot._parse_tg_response("getUpdates", response)

    response.json.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_polling_http_409_is_not_a_polling_conflict():
    bot = _bot()
    response = MagicMock()
    response.status = 409
    response.json = AsyncMock(return_value={"ok": False})

    assert await bot._parse_tg_response("sendMessage", response) is None
    response.json.assert_awaited_once()


def test_operational_source_does_not_log_chat_identity():
    source = open(control_bot_module.__file__, encoding="utf-8").read()

    assert "chat_id=%s" not in source
