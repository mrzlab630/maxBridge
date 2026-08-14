"""Single MAX account — wraps session + connection for one account."""

import logging
from typing import Any

from maxbridge.auth.encryption import TokenEncryptor
from maxbridge.auth.qr_auth import authenticate_qr
from maxbridge.auth.session import Session
from maxbridge.auth.sms_auth import request_sms_code, verify_sms_code
from maxbridge.auth.token_auth import login_with_token
from maxbridge.client.connection import MaxConnection
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.client.account")


class Account:
    """Represents a single authenticated MAX account."""

    def __init__(self, account_id: str, config: dict[str, Any],
                 encryptor: TokenEncryptor) -> None:
        self.account_id = account_id
        self._config = config
        self._phone = config.get("phone", "")

        session_path = config.get("session_file", f"data/{account_id}.session")
        self._session = Session(session_path, encryptor)

        self._connection = MaxConnection(
            session=self._session,
            reconnect_delay=config.get("reconnect_delay", 5),
            max_retries=config.get("reconnect_max_retries", 10),
        )

    @property
    def connection(self) -> MaxConnection:
        return self._connection

    @property
    def session(self) -> Session:
        return self._session

    @property
    def phone(self) -> str:
        return self._phone

    @property
    def is_connected(self) -> bool:
        return self._connection.is_connected

    def set_packet_callback(self, callback: PacketHandler) -> None:
        self._connection.set_packet_callback(callback)

    async def connect(self) -> None:
        await self._connection.connect()
        logger.info("Account '%s' connected", self.account_id)

    async def disconnect(self) -> None:
        await self._connection.disconnect()
        logger.info("Account '%s' disconnected", self.account_id)

    async def verify_identity(self) -> str | None:
        """Return stored identity or backfill it through bounded token login."""
        if self._session.max_contact_id is not None:
            return self._session.max_contact_id
        if not self._session.device_id or not self._session.token:
            return None

        client = await self._connection.create_raw_client()
        try:
            await login_with_token(
                client,
                self._session,
                clear_session_on_rejection=False,
            )
            return self._session.max_contact_id
        finally:
            await self._connection.release_raw_client()

    async def authenticate_qr(self) -> None:
        """QR code auth — user scans with MAX mobile app."""
        client = await self._connection.create_raw_client()
        try:
            await authenticate_qr(client, self._session)
        finally:
            await self._connection.release_raw_client()

    async def authenticate_sms(self, phone: str | None = None) -> None:
        """SMS auth — for regions where phone auth is still enabled."""
        target_phone = phone or self._phone
        client = await self._connection.create_raw_client()
        try:
            sms_token = await request_sms_code(client, target_phone)
            self._pending_sms_token = sms_token
        except Exception:
            await self._connection.release_raw_client()
            raise

    async def verify_sms(self, code: int) -> None:
        if not hasattr(self, "_pending_sms_token"):
            raise RuntimeError("Call authenticate_sms() first")
        client = self._connection.client
        try:
            await verify_sms_code(client, self._pending_sms_token, code, self._session)
        finally:
            del self._pending_sms_token
            await self._connection.release_raw_client()

    def has_session(self) -> bool:
        return self._session.exists()

    def load_session(self) -> bool:
        return self._session.load()
