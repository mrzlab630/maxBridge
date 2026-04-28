"""Tests for account manager startup connection recovery."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.client.account_manager import AccountManager
from maxbridge.protocol.errors import MaxAuthRequiredError


class TestAccountManagerConnectAll:
    @pytest.mark.asyncio
    async def test_connect_all_starts_reconnect_after_transient_failure(self):
        manager = AccountManager(MagicMock())
        account = MagicMock()
        account.load_session.return_value = True
        account.connect = AsyncMock(side_effect=TimeoutError("opening handshake"))
        account.connection.reconnect_nowait = MagicMock(return_value=True)
        manager._accounts = {"default": account}

        await manager.connect_all()

        account.connection.reconnect_nowait.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_connect_all_does_not_reconnect_after_auth_required(self):
        manager = AccountManager(MagicMock())
        account = MagicMock()
        account.load_session.return_value = True
        account.connect = AsyncMock(side_effect=MaxAuthRequiredError("token expired"))
        account.connection.reconnect_nowait = MagicMock()
        manager._accounts = {"default": account}

        await manager.connect_all()

        account.connection.reconnect_nowait.assert_not_called()
