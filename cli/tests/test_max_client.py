"""Tests for MAX transport configuration overrides."""

from unittest.mock import AsyncMock

import pytest

from maxbridge.protocol.max_client import MaxClient


class _DummyConnection:
    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        return None


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
