"""Multi-account manager — registry of MAX accounts with lifecycle control."""

import logging
from typing import Any, Callable

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.client.account import Account
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.client.account_manager")


class AccountManager:
    """Manages multiple MAX accounts — connect, disconnect, route events."""

    def __init__(self, encryptor: TokenEncryptor) -> None:
        self._encryptor = encryptor
        self._accounts: dict[str, Account] = {}
        self._on_fatal_callback: Callable[[], None] | None = None
        self._on_auth_required_callback: Callable[[str, Exception], None] | None = None

    @property
    def accounts(self) -> dict[str, Account]:
        return dict(self._accounts)

    @property
    def account_ids(self) -> list[str]:
        return list(self._accounts.keys())

    def set_on_fatal(self, callback: Callable[[], None]) -> None:
        """Set callback for unrecoverable connection failure on any account."""
        self._on_fatal_callback = callback

    def set_on_auth_required(self,
                             callback: Callable[[str, Exception], None]) -> None:
        """Set callback for expired/invalid auth on any account."""
        self._on_auth_required_callback = callback
        for account_id, account in self._accounts.items():
            account.connection.set_on_auth_required(
                lambda exc, aid=account_id: callback(aid, exc),
            )

    def add_account(self, account_id: str, config: dict[str, Any]) -> Account:
        """Register a new account from config. Does not connect yet."""
        if account_id in self._accounts:
            raise ValueError(f"Account '{account_id}' already registered")

        account = Account(account_id, config, self._encryptor)
        if self._on_fatal_callback:
            account.connection.set_on_fatal(self._on_fatal_callback)
        if self._on_auth_required_callback:
            account.connection.set_on_auth_required(
                lambda exc, aid=account_id: self._on_auth_required_callback(aid, exc),
            )

        self._accounts[account_id] = account
        logger.info("Account '%s' registered", account_id)
        return account

    def get(self, account_id: str) -> Account | None:
        """Get account by ID. Returns None if not found."""
        return self._accounts.get(account_id)

    def require(self, account_id: str) -> Account:
        """Get account by ID. Raises ValueError if not found."""
        account = self._accounts.get(account_id)
        if account is None:
            raise ValueError(f"Unknown account: '{account_id}'")
        return account

    def set_packet_callback(self, callback: PacketHandler) -> None:
        """Set packet callback for ALL accounts."""
        for account in self._accounts.values():
            account.set_packet_callback(callback)

    async def connect_all(self) -> None:
        """Connect all accounts that have saved sessions."""
        for account_id, account in self._accounts.items():
            if account.load_session():
                try:
                    await account.connect()
                except Exception:
                    logger.exception("Failed to connect account '%s'", account_id)
            else:
                logger.warning("Account '%s' has no session — run --auth-only", account_id)

    async def disconnect_all(self) -> None:
        """Disconnect all connected accounts."""
        for account_id, account in self._accounts.items():
            try:
                await account.disconnect()
            except Exception as e:
                logger.warning("Error disconnecting '%s': %s", account_id, e)

    def status(self) -> dict[str, dict[str, Any]]:
        """Return status of all accounts."""
        return {
            aid: {
                "connected": acc.is_connected,
                "phone": acc.phone,
                "has_session": acc.session.exists(),
            }
            for aid, acc in self._accounts.items()
        }
