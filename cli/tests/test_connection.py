"""Tests for MAX connection auth-failure handling."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.auth.session import Session
from maxbridge.client.connection import MaxConnection
from maxbridge.protocol.errors import MaxAuthRequiredError


class TestMaxConnection:
    @pytest.mark.asyncio
    async def test_connect_notifies_auth_required(self, monkeypatch, tmp_dir, encryptor):
        session = Session(os.path.join(tmp_dir, "auth.session"), encryptor)
        session.save("dev123", "tok123")

        fake_client = MagicMock()
        fake_client.connect = AsyncMock()
        fake_client.disconnect = AsyncMock()
        fake_client.set_reconnect_callback = MagicMock()
        fake_client.set_packet_callback = MagicMock()

        login = AsyncMock(side_effect=MaxAuthRequiredError("token expired"))
        monkeypatch.setattr("maxbridge.client.connection.MaxClient", lambda: fake_client)
        monkeypatch.setattr("maxbridge.client.connection.login_with_token", login)

        conn = MaxConnection(session)
        callback = MagicMock()
        conn.set_on_auth_required(callback)

        with pytest.raises(MaxAuthRequiredError):
            await conn.connect()

        fake_client.disconnect.assert_awaited_once()
        callback.assert_called_once()
        assert conn.is_connected is False
