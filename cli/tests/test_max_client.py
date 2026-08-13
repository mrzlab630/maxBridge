"""Tests for MAX transport configuration overrides."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from maxbridge.protocol.errors import MaxConnectionError
from maxbridge.protocol.max_client import MaxClient


class _DummyConnection:
    async def send(self, data):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        return None


class _SlowSendConnection(_DummyConnection):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, data):
        self.started.set()
        await self.release.wait()


class TestMaxClientConfig:
    @pytest.mark.asyncio
    async def test_connect_uses_configured_transport(self, monkeypatch):
        captured = {}

        async def fake_connect(url, additional_headers):
            captured["url"] = url
            captured["headers"] = dict(additional_headers)
            return _DummyConnection()

        monkeypatch.setattr("maxbridge.protocol.max_client.websockets.connect", fake_connect)

        client = MaxClient(config={
            "max_client": {
                "ws_host": "wss://example.test/socket",
                "ws_origin": "https://origin.example.test",
                "user_agent": "TestAgent/1.0",
            },
        })

        await client.connect()
        await client.disconnect()

        assert captured["url"] == "wss://example.test/socket"
        assert captured["headers"] == {
            "Origin": "https://origin.example.test",
            "User-Agent": "TestAgent/1.0",
        }

    @pytest.mark.asyncio
    async def test_send_hello_uses_configured_client_fingerprint(self):
        client = MaxClient(config={
            "max_client": {
                "app_version": "99.1.2",
                "user_agent": "BridgeTest/9.9",
                "hello": {
                    "device_type": "DESKTOP",
                    "locale": "ru",
                    "device_locale": "ru",
                    "os_version": "Ubuntu 24.04",
                    "device_name": "Bridge QA",
                    "screen": "2560x1440 1.0x",
                    "timezone": "UTC",
                },
            },
        })
        client.invoke_method = AsyncMock(return_value={"payload": {}})

        await client.send_hello("device-123")

        client.invoke_method.assert_awaited_once_with(
            opcode=6,
            payload={
                "userAgent": {
                    "deviceType": "DESKTOP",
                    "locale": "ru",
                    "osVersion": "Ubuntu 24.04",
                    "deviceName": "Bridge QA",
                    "headerUserAgent": "BridgeTest/9.9",
                    "deviceLocale": "ru",
                    "appVersion": "99.1.2",
                    "screen": "2560x1440 1.0x",
                    "timezone": "UTC",
                },
                "deviceId": "device-123",
            },
        )
        assert client.device_id == "device-123"

    @pytest.mark.asyncio
    async def test_invoke_method_disconnect_raises_max_connection_error(self):
        client = MaxClient(config={"max_client": {}})
        client._connection = _DummyConnection()

        task = asyncio.create_task(client.invoke_method(1, {}, timeout=30))
        await asyncio.sleep(0)
        await client.disconnect()

        with pytest.raises(MaxConnectionError, match="Disconnected"):
            await task
        assert client._pending == {}

    @pytest.mark.asyncio
    async def test_invoke_method_preserves_cancellation_during_disconnect(self):
        client = MaxClient(config={"max_client": {}})
        client._connection = _DummyConnection()

        task = asyncio.create_task(client.invoke_method(1, {}, timeout=30))
        await asyncio.sleep(0)
        task.cancel()
        await client.disconnect()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending == {}

    @pytest.mark.asyncio
    async def test_invoke_method_cleans_pending_when_cancelled_during_send(self):
        client = MaxClient(config={"max_client": {}})
        client._connection = _SlowSendConnection()

        task = asyncio.create_task(client.invoke_method(1, {}, timeout=30))
        await client._connection.started.wait()
        assert client._pending

        task.cancel()
        client._connection.release.set()

        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._pending == {}

    @pytest.mark.asyncio
    async def test_disconnect_waits_for_background_tasks(self):
        client = MaxClient(config={"max_client": {}})
        client._connection = _DummyConnection()

        recv_cancelled = asyncio.Event()
        keepalive_cancelled = asyncio.Event()

        async def _background(cancelled: asyncio.Event) -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        client._recv_task = asyncio.create_task(_background(recv_cancelled))
        client._keepalive_task = asyncio.create_task(_background(keepalive_cancelled))
        await asyncio.sleep(0)

        await client.disconnect()

        assert recv_cancelled.is_set()
        assert keepalive_cancelled.is_set()
        assert client._recv_task is None
        assert client._keepalive_task is None
