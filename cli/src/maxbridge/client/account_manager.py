"""Multi-account manager — registry of MAX accounts with lifecycle control."""

import asyncio
import logging
from typing import Any, Callable

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.identity import (
    MaxIdentityUnavailableError,
    normalize_max_contact_id,
)
from maxbridge.client.account import Account
from maxbridge.client.connection import ReconnectDisposition
from maxbridge.protocol.errors import MaxAuthRequiredError
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.client.account_manager")


class AccountManager:
    """Manages multiple MAX accounts — connect, disconnect, route events."""

    def __init__(self, encryptor: TokenEncryptor) -> None:
        self._encryptor = encryptor
        self._accounts: dict[str, Account] = {}
        self._packet_callbacks: dict[str, PacketHandler] = {}
        self._account_states: dict[str, dict[str, Any]] = {}
        self._session_accounts: set[str] = set()
        self._callback_activated: set[str] = set()
        self._arbitration_lock = asyncio.Lock()
        self._shutdown_requested = False
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
        self._account_states[account_id] = {
            "state": "pending",
            "duplicate_of": None,
        }
        self._bind_reconnect_lifecycle(account_id, account)
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

    def set_packet_callback(self, account_id: str, callback: PacketHandler) -> None:
        """Register an account-scoped callback for later canonical activation."""
        self.require(account_id)
        self._packet_callbacks[account_id] = callback
        if self._account_states[account_id]["state"] == "canonical":
            account = self._accounts[account_id]
            account.set_packet_callback(callback)
            self._callback_activated.add(account_id)
            account.connection.set_producer_enabled(True)

    async def find_duplicate_account(
        self,
        max_contact_id: str,
        *,
        exclude_account_id: str | None = None,
    ) -> str | None:
        """Find an earlier saved session owning the same verified MAX identity."""
        identity = normalize_max_contact_id(max_contact_id)
        if identity is None:
            raise MaxIdentityUnavailableError("Некорректный идентификатор профиля MAX")

        for account_id, account in self._accounts.items():
            if account_id == exclude_account_id or not account.load_session():
                continue
            try:
                candidate = await account.verify_identity()
            except MaxAuthRequiredError:
                continue
            except Exception as exc:
                logger.warning(
                    "Identity verification unavailable for account '%s': %s",
                    account_id,
                    exc.__class__.__name__,
                )
                raise MaxIdentityUnavailableError(
                    f"Не удалось проверить существующую сессию '{account_id}'. "
                    "Новая авторизация не сохранена."
                ) from exc

            if candidate is None:
                raise MaxIdentityUnavailableError(
                    f"Сессия '{account_id}' не вернула проверенный профиль MAX. "
                    "Новая авторизация не сохранена."
                )
            if candidate == identity:
                return account_id
        return None

    async def connect_all(self) -> None:
        """Sequentially connect accounts and activate first-wins producers."""
        self._shutdown_requested = False
        for account_id, account in self._accounts.items():
            self._bind_reconnect_lifecycle(account_id, account)
            if account.load_session():
                self._session_accounts.add(account_id)
                try:
                    await account.connect()
                except MaxAuthRequiredError:
                    self._session_accounts.discard(account_id)
                    self._set_state(account_id, "auth_required")
                    logger.warning("Account '%s' requires re-authentication", account_id)
                except Exception as exc:
                    self._set_state(account_id, "connection_error")
                    reconnecting = account.connection.reconnect_nowait()
                    state = (
                        "background reconnect started"
                        if reconnecting
                        else "reconnect already active"
                    )
                    logger.warning(
                        "Initial connect failed for account '%s': %s: %s; %s",
                        account_id,
                        exc.__class__.__name__,
                        exc,
                        state,
                    )
                    continue

                disposition = await self._authenticated(account_id)
                if disposition == "rejected":
                    await account.disconnect()
            else:
                self._session_accounts.discard(account_id)
                self._set_state(account_id, "no_session")
                logger.warning("Account '%s' has no session — run --auth-only", account_id)

    async def disconnect_all(self) -> None:
        """Disconnect all connected accounts."""
        self._shutdown_requested = True
        for account_id, account in self._accounts.items():
            account.connection.set_producer_enabled(False)
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
                "canonical": self._account_states[aid]["state"] == "canonical",
                "duplicate_of": self._account_states[aid]["duplicate_of"],
                "reason": self._account_states[aid]["state"],
            }
            for aid, acc in self._accounts.items()
        }

    def _set_state(
        self,
        account_id: str,
        state: str,
        duplicate_of: str | None = None,
    ) -> None:
        self._account_states[account_id] = {
            "state": state,
            "duplicate_of": duplicate_of,
        }

    def _bind_reconnect_lifecycle(self, account_id: str, account: Account) -> None:
        account.connection.set_on_reconnect_authenticated(
            lambda aid=account_id: self._authenticated(aid),
        )

    async def _authenticated(self, account_id: str) -> ReconnectDisposition:
        disconnect_ids: list[str] = []
        async with self._arbitration_lock:
            state = self._account_states[account_id]["state"]
            if self._shutdown_requested or state in {
                "auth_required",
                "duplicate",
                "identity_unavailable",
                "no_session",
            }:
                return "rejected"

            self._set_state(account_id, "authenticated_pending")
            decisions = self._arbitrate_authenticated_accounts()
            for candidate_id, disposition in decisions.items():
                account = self._accounts[candidate_id]
                if disposition == "approved":
                    self._activate_account(candidate_id)
                else:
                    account.connection.set_producer_enabled(False)
                    if disposition == "rejected" and candidate_id != account_id:
                        disconnect_ids.append(candidate_id)
            disposition = decisions[account_id]

        for candidate_id in disconnect_ids:
            await self._accounts[candidate_id].disconnect()
        return disposition

    def _arbitrate_authenticated_accounts(
        self,
    ) -> dict[str, ReconnectDisposition]:
        decisions: dict[str, ReconnectDisposition] = {}
        identity_reservations: dict[str, str] = {}
        unresolved_identity_before = False

        for account_id, account in self._accounts.items():
            if account_id not in self._session_accounts:
                continue

            state = self._account_states[account_id]["state"]
            if state in {
                "auth_required",
                "duplicate",
                "identity_unavailable",
                "no_session",
            }:
                continue

            identity = normalize_max_contact_id(account.session.max_contact_id)
            if state in {"pending", "connection_error"}:
                if identity is None:
                    unresolved_identity_before = True
                else:
                    identity_reservations.setdefault(identity, account_id)
                continue

            if identity is None:
                self._set_state(account_id, "identity_unavailable")
                decisions[account_id] = "rejected"
                logger.warning(
                    "Account '%s' cannot produce events: verified identity unavailable",
                    account_id,
                )
                continue

            duplicate_of = identity_reservations.get(identity)
            if duplicate_of is not None:
                self._set_state(account_id, "duplicate", duplicate_of)
                decisions[account_id] = "rejected"
                logger.warning(
                    "Account '%s' duplicates canonical account '%s'; producer disabled",
                    account_id,
                    duplicate_of,
                )
                continue

            if unresolved_identity_before:
                self._set_state(account_id, "authenticated_pending")
                decisions[account_id] = "pending"
                continue

            identity_reservations[identity] = account_id
            self._set_state(account_id, "canonical")
            decisions[account_id] = "approved"

        return decisions

    def _activate_account(self, account_id: str) -> None:
        account = self._accounts[account_id]
        callback = self._packet_callbacks.get(account_id)
        if callback is not None and account_id not in self._callback_activated:
            account.set_packet_callback(callback)
            self._callback_activated.add(account_id)
        account.connection.set_producer_enabled(True)
