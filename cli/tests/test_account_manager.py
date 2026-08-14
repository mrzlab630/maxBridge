"""Tests for account manager startup connection recovery."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.client.account_manager import AccountManager
from maxbridge.client.connection import MaxConnection
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


def _fake_client(
    *,
    identity: str | None = None,
    connect_error: Exception | None = None,
    connect_gate: asyncio.Event | None = None,
) -> MagicMock:
    client = MagicMock()
    client.auth_identity = identity

    async def connect() -> None:
        if connect_gate is not None:
            await connect_gate.wait()
        if connect_error is not None:
            raise connect_error

    client.connect = AsyncMock(side_effect=connect)
    client.disconnect = AsyncMock()
    client.set_reconnect_callback = MagicMock()
    client.set_packet_callback = MagicMock()
    return client


def _reconnecting_account(
    connection: MaxConnection,
    session: MagicMock,
) -> MagicMock:
    account = MagicMock()
    account.connection = connection
    account.session = session
    account.load_session.return_value = True
    account.session.exists.return_value = True
    account.is_connected = False
    account.phone = ""
    account.connect = AsyncMock(side_effect=connection.connect)
    account.disconnect = AsyncMock(side_effect=connection.disconnect)
    account.set_packet_callback = MagicMock(side_effect=connection.set_packet_callback)
    return account


def _install_client_factory(monkeypatch, clients: list[MagicMock]) -> None:
    client_iter = iter(clients)
    monkeypatch.setattr(
        "maxbridge.client.connection.MaxClient",
        lambda: next(client_iter),
    )

    async def login(client: MagicMock, session: MagicMock) -> None:
        session.max_contact_id = client.auth_identity

    monkeypatch.setattr("maxbridge.client.connection.login_with_token", login)


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
    async def test_transient_reconnect_activates_canonical_callback_once(
        self,
        monkeypatch,
    ):
        initial = _fake_client(connect_error=TimeoutError("opening handshake"))
        recovered = _fake_client(identity="701")
        _install_client_factory(monkeypatch, [initial, recovered])

        manager = AccountManager(MagicMock())
        session = MagicMock(max_contact_id=None)
        connection = MaxConnection(session, reconnect_delay=0, max_retries=1)
        account = _reconnecting_account(connection, session)
        _install(manager, default=account)
        callback = AsyncMock()
        manager.set_packet_callback("default", callback)

        await manager.connect_all()
        reconnect_task = connection._reconnect_task
        assert reconnect_task is not None
        await reconnect_task

        assert manager.status()["default"]["canonical"] is True
        account.set_packet_callback.assert_called_once_with(callback)
        initial.set_packet_callback.assert_not_called()
        recovered.set_packet_callback.assert_called_once_with(callback)

    @pytest.mark.asyncio
    async def test_later_duplicate_cannot_win_by_reconnecting_first(
        self,
        monkeypatch,
    ):
        release_first = asyncio.Event()
        first_initial = _fake_client(connect_error=TimeoutError("first handshake"))
        later_initial = _fake_client(connect_error=TimeoutError("later handshake"))
        first_reconnect = _fake_client(identity="801", connect_gate=release_first)
        later_reconnect = _fake_client(identity="801")
        _install_client_factory(
            monkeypatch,
            [first_initial, later_initial, first_reconnect, later_reconnect],
        )

        manager = AccountManager(MagicMock())
        first_session = MagicMock(max_contact_id="801")
        later_session = MagicMock(max_contact_id="801")
        first_connection = MaxConnection(
            first_session,
            reconnect_delay=0,
            max_retries=1,
        )
        later_connection = MaxConnection(
            later_session,
            reconnect_delay=0,
            max_retries=1,
        )
        first = _reconnecting_account(first_connection, first_session)
        later = _reconnecting_account(later_connection, later_session)
        _install(manager, default=first, account_2=later)
        first_callback = AsyncMock()
        later_callback = AsyncMock()
        manager.set_packet_callback("default", first_callback)
        manager.set_packet_callback("account_2", later_callback)

        await manager.connect_all()
        first_task = first_connection._reconnect_task
        later_task = later_connection._reconnect_task
        assert first_task is not None
        assert later_task is not None
        await later_task

        later_reconnect.set_packet_callback.assert_not_called()
        later_reconnect.disconnect.assert_awaited_once_with()
        assert manager.status()["account_2"]["duplicate_of"] == "default"

        release_first.set()
        await first_task

        first_reconnect.set_packet_callback.assert_called_once_with(first_callback)
        later.set_packet_callback.assert_not_called()
        assert manager.status()["default"]["canonical"] is True

        await manager.disconnect_all()
        assert later_connection.reconnect_nowait() is False

    @pytest.mark.asyncio
    async def test_distinct_reconnect_waits_for_earlier_unknown_identity(
        self,
        monkeypatch,
    ):
        release_first = asyncio.Event()
        first_initial = _fake_client(connect_error=TimeoutError("first handshake"))
        later_initial = _fake_client(connect_error=TimeoutError("later handshake"))
        first_reconnect = _fake_client(identity="901", connect_gate=release_first)
        later_reconnect = _fake_client(identity="902")
        _install_client_factory(
            monkeypatch,
            [first_initial, later_initial, first_reconnect, later_reconnect],
        )

        manager = AccountManager(MagicMock())
        first_session = MagicMock(max_contact_id=None)
        later_session = MagicMock(max_contact_id=None)
        first_connection = MaxConnection(
            first_session,
            reconnect_delay=0,
            max_retries=1,
        )
        later_connection = MaxConnection(
            later_session,
            reconnect_delay=0,
            max_retries=1,
        )
        first = _reconnecting_account(first_connection, first_session)
        later = _reconnecting_account(later_connection, later_session)
        _install(manager, default=first, account_2=later)
        first_callback = AsyncMock()
        later_callback = AsyncMock()
        manager.set_packet_callback("default", first_callback)
        manager.set_packet_callback("account_2", later_callback)

        await manager.connect_all()
        first_task = first_connection._reconnect_task
        later_task = later_connection._reconnect_task
        assert first_task is not None
        assert later_task is not None
        await later_task

        assert later_connection.is_connected is True
        assert manager.status()["account_2"]["reason"] == "authenticated_pending"
        later.set_packet_callback.assert_not_called()
        later_reconnect.set_packet_callback.assert_not_called()

        release_first.set()
        await first_task

        first_reconnect.set_packet_callback.assert_called_once_with(first_callback)
        later_reconnect.set_packet_callback.assert_called_once_with(later_callback)
        assert manager.status()["default"]["canonical"] is True
        assert manager.status()["account_2"]["canonical"] is True

    @pytest.mark.asyncio
    async def test_reconnect_with_unknown_identity_stays_non_producing(
        self,
        monkeypatch,
    ):
        initial = _fake_client(connect_error=TimeoutError("opening handshake"))
        recovered = _fake_client(identity=None)
        _install_client_factory(monkeypatch, [initial, recovered])

        manager = AccountManager(MagicMock())
        session = MagicMock(max_contact_id=None)
        connection = MaxConnection(session, reconnect_delay=0, max_retries=1)
        account = _reconnecting_account(connection, session)
        _install(manager, default=account)
        manager.set_packet_callback("default", AsyncMock())

        await manager.connect_all()
        reconnect_task = connection._reconnect_task
        assert reconnect_task is not None
        await reconnect_task

        assert connection.is_connected is False
        recovered.set_packet_callback.assert_not_called()
        recovered.disconnect.assert_awaited_once_with()
        assert manager.status()["default"]["reason"] == "identity_unavailable"

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
