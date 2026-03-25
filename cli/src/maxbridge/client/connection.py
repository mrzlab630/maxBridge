"""MaxClient wrapper with auto-reconnect, heartbeat, and lifecycle management."""

import asyncio
import logging
from typing import Any, Callable

from maxbridge.auth.session import Session
from maxbridge.auth.token_auth import login_with_token
from maxbridge.protocol.max_client import MaxClient
from maxbridge.utils.constants import Opcode
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.client.connection")


class MaxConnection:
    """Manages the MaxClient lifecycle: connect, auth, reconnect, heartbeat."""

    def __init__(self, session: Session, reconnect_delay: int = 5,
                 max_retries: int = 10) -> None:
        self._session = session
        self._reconnect_delay = reconnect_delay
        self._max_retries = max_retries
        self._client: MaxClient | None = None
        self._packet_callback: PacketHandler | None = None
        self._connected = False
        self._on_fatal_callback: Callable[[], None] | None = None
        self._reconnecting = False

    @property
    def client(self) -> MaxClient:
        if self._client is None:
            raise RuntimeError("Not connected — call connect() first")
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._connected

    def set_on_fatal(self, callback: Callable[[], None]) -> None:
        self._on_fatal_callback = callback

    async def create_raw_client(self) -> MaxClient:
        """Create and connect a raw MaxClient (for initial SMS auth)."""
        client = MaxClient()
        await client.connect()
        self._client = client
        return client

    async def release_raw_client(self) -> None:
        """Disconnect and release a raw client."""
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def connect(self) -> MaxClient:
        """Create client, connect, authenticate with saved token."""
        self._client = MaxClient()
        await self._client.connect()

        await login_with_token(self._client, self._session)
        self._connected = True

        self._client.set_reconnect_callback(self._on_reconnect)
        if self._packet_callback:
            self._client.set_packet_callback(self._packet_callback)

        logger.info("Connected and authenticated to MAX")
        return self._client

    def set_packet_callback(self, callback: PacketHandler) -> None:
        self._packet_callback = callback
        if self._client:
            self._client.set_packet_callback(callback)

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning("Disconnect error: %s", e)
            self._client = None
            self._connected = False
            logger.info("Disconnected from MAX")

    async def send_message(self, chat_id: int, text: str,
                           **kwargs) -> dict[str, Any]:
        """Send a text message."""
        message: dict[str, Any] = {"text": text}
        attaches = kwargs.get("attaches")
        if attaches:
            message["attaches"] = attaches
        return await self.client.invoke_method(
            opcode=Opcode.SEND_MESSAGE,
            payload={"chatId": chat_id, "message": message},
        )

    async def get_chat_history(self, chat_id: int, count: int = 30,
                               from_cid: int | None = None) -> dict[str, Any]:
        """Fetch history. from_cid=None uses current time as anchor."""
        if from_cid is None:
            import time
            from_cid = int(time.time() * 1000)
        return await self.client.invoke_method(
            opcode=Opcode.GET_HISTORY,
            payload={
                "chatId": chat_id,
                "from": from_cid,
                "forward": 0,
                "backward": count,
                "getMessages": True,
            },
        )

    async def resolve_chat(self, chat_id: int) -> dict[str, Any]:
        return await self.client.invoke_method(
            opcode=Opcode.RESOLVE_CHAT,
            payload={"chatIds": [chat_id]},
        )

    async def leave_chat(self, chat_id: int) -> dict[str, Any]:
        """Отписаться от канала/чата."""
        return await self.client.invoke_method(
            opcode=Opcode.LEAVE_CHAT,
            payload={"chatId": chat_id},
        )

    async def resolve_users(self, user_ids: list[int]) -> dict[str, Any]:
        return await self.client.invoke_method(
            opcode=Opcode.RESOLVE_USERS,
            payload={"contactIds": user_ids},
        )

    async def _on_reconnect(self) -> None:
        """Reconnect with exponential backoff. Inline client creation."""
        if self._reconnecting:
            return
        self._reconnecting = True
        self._connected = False

        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

        for attempt in range(1, self._max_retries + 1):
            delay = min(self._reconnect_delay * attempt, 60)
            logger.warning("Reconnecting in %ds (attempt %d/%d)...",
                           delay, attempt, self._max_retries)
            await asyncio.sleep(delay)
            try:
                client = MaxClient()
                await client.connect()
                await login_with_token(client, self._session)

                self._client = client
                self._connected = True
                client.set_reconnect_callback(self._on_reconnect)
                if self._packet_callback:
                    client.set_packet_callback(self._packet_callback)

                self._reconnecting = False
                logger.info("Reconnected on attempt %d", attempt)
                return
            except Exception as e:
                logger.error("Reconnect attempt %d failed: %s", attempt, e)

        self._reconnecting = False
        logger.critical("Failed to reconnect after %d attempts", self._max_retries)
        if self._on_fatal_callback:
            self._on_fatal_callback()
