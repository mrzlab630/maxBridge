"""Tests for account manager startup connection recovery."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.client.account_manager import AccountManager
from maxbridge.protocol.errors import MaxAuthRequiredError


def _mock_account(identity: str | None, *, has_session: bool = True) -> MagicMock:
    account = MagicMock()
    account.load_session.return_value = has_session
    account.connect = AsyncMock()
    account.disconnect = AsyncMock()
    account.set_packet_callback = MagicMock()
    account.connection.reconnect_nowait = MagicMock(return_value=True)
    account.session.max_contact_id = identity
    account.session.exists.return_value = has_session
    account.is_connected = has_session
    account.phone = ""
    return account


def _install(manager: AccountManager, **accounts: MagicMock) -> None:
    manager._accounts = dict(accounts)
    manager._account_states = {
        account_id: {"state": "pending", "duplicate_of": None}
        for account_id in accounts
    }


class TestAccountManagerConnectAll:
    @pytest.mark.asyncio
    async def test_connect_all_starts_reconnect_after_transient_failure(self):
        manager = AccountManager(MagicMock())
        account = _mock_account(None)
        account.connect = AsyncMock(side_effect=TimeoutError("opening handshake"))
        _install(manager, default=account)

        await manager.connect_all()

        account.connection.reconnect_nowait.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_connect_all_does_not_reconnect_after_auth_required(self):
        manager = AccountManager(MagicMock())
        account = _mock_account(None)
        account.connect = AsyncMock(side_effect=MaxAuthRequiredError("token expired"))
        account.connection.reconnect_nowait = MagicMock()
        _install(manager, default=account)

        await manager.connect_all()

        account.connection.reconnect_nowait.assert_not_called()

    @pytest.mark.asyncio
    async def test_legacy_duplicate_startup_is_deterministic_first_wins(self):
        manager = AccountManager(MagicMock())
        default = _mock_account(None)
        duplicate = _mock_account(None)

        async def connect_default():
            default.session.max_contact_id = "700"

        async def connect_duplicate():
            duplicate.session.max_contact_id = "700"

        default.connect.side_effect = connect_default
        duplicate.connect.side_effect = connect_duplicate
        _install(manager, default=default, account_2=duplicate)
        default_callback = AsyncMock()
        duplicate_callback = AsyncMock()
        manager.set_packet_callback("default", default_callback)
        manager.set_packet_callback("account_2", duplicate_callback)

        await manager.connect_all()

        default.set_packet_callback.assert_called_once_with(default_callback)
        duplicate.set_packet_callback.assert_not_called()
        duplicate.disconnect.assert_awaited_once_with()
        duplicate.connection.reconnect_nowait.assert_not_called()
        status = manager.status()
        assert status["default"]["canonical"] is True
        assert status["account_2"]["duplicate_of"] == "default"
        assert status["account_2"]["reason"] == "duplicate"

    @pytest.mark.asyncio
    async def test_distinct_identities_are_both_canonical(self):
        manager = AccountManager(MagicMock())
        default = _mock_account("700")
        account_2 = _mock_account("701")
        _install(manager, default=default, account_2=account_2)
        callbacks = {"default": AsyncMock(), "account_2": AsyncMock()}
        for account_id, callback in callbacks.items():
            manager.set_packet_callback(account_id, callback)

        await manager.connect_all()

        default.set_packet_callback.assert_called_once_with(callbacks["default"])
        account_2.set_packet_callback.assert_called_once_with(callbacks["account_2"])
        assert manager.status()["default"]["canonical"] is True
        assert manager.status()["account_2"]["canonical"] is True

    @pytest.mark.asyncio
    async def test_missing_session_does_not_reserve_identity(self):
        manager = AccountManager(MagicMock())
        missing = _mock_account("700", has_session=False)
        canonical = _mock_account("700")
        _install(manager, default=missing, account_2=canonical)
        callback = AsyncMock()
        manager.set_packet_callback("account_2", callback)

        await manager.connect_all()

        missing.connect.assert_not_awaited()
        canonical.set_packet_callback.assert_called_once_with(callback)
        assert manager.status()["default"]["reason"] == "no_session"
        assert manager.status()["account_2"]["canonical"] is True

    @pytest.mark.asyncio
    async def test_unknown_identity_is_disconnected_without_producer(self):
        manager = AccountManager(MagicMock())
        account = _mock_account(None)
        _install(manager, default=account)
        manager.set_packet_callback("default", AsyncMock())

        await manager.connect_all()

        account.set_packet_callback.assert_not_called()
        account.disconnect.assert_awaited_once_with()
        assert manager.status()["default"]["reason"] == "identity_unavailable"

    @pytest.mark.asyncio
    async def test_duplicate_lookup_preserves_canonical_session_bytes(
        self, tmp_dir, encryptor
    ):
        manager = AccountManager(encryptor)
        first_path = os.path.join(tmp_dir, "default.session")
        new_path = os.path.join(tmp_dir, "account_2.session")
        first = manager.add_account("default", {"session_file": first_path})
        new = manager.add_account("account_2", {"session_file": new_path})
        first.session.save("first-device", "first-token", "800")
        new.session.save("new-device", "new-token", "800")
        before = open(first_path, "rb").read()

        duplicate_of = await manager.find_duplicate_account(
            "800",
            exclude_account_id="account_2",
        )
        new.session.clear()

        assert duplicate_of == "default"
        assert open(first_path, "rb").read() == before
        assert first.session.exists() is True
        assert new.session.exists() is False

    @pytest.mark.asyncio
    async def test_canonical_choice_is_stable_for_same_restart_order(self):
        winners = []
        for _ in range(2):
            manager = AccountManager(MagicMock())
            default = _mock_account("900")
            account_2 = _mock_account("900")
            _install(manager, default=default, account_2=account_2)

            await manager.connect_all()

            status = manager.status()
            winners.append(next(aid for aid, item in status.items() if item["canonical"]))
        assert winners == ["default", "default"]

    @pytest.mark.asyncio
    async def test_status_exposes_only_safe_canonical_metadata(self):
        manager = AccountManager(MagicMock())
        default = _mock_account("secret-contact-id")
        account_2 = _mock_account("secret-contact-id")
        default.session.max_contact_id = "123456"
        account_2.session.max_contact_id = "123456"
        _install(manager, default=default, account_2=account_2)

        await manager.connect_all()

        rendered = repr(manager.status())
        assert "123456" not in rendered
        assert "token" not in rendered.lower()
        assert manager.status()["account_2"]["duplicate_of"] == "default"
