"""MaxClient wrapper with auto-reconnect, heartbeat, and lifecycle management."""

import asyncio
import contextlib
import logging
from typing import Any, Callable

from maxbridge.auth.session import Session
from maxbridge.auth.token_auth import login_with_token
from maxbridge.protocol.errors import MaxAuthRequiredError
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
        self._on_auth_required_callback: Callable[[Exception], None] | None = None
        self._reconnecting = False
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown_requested = False

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

    def set_on_auth_required(self, callback: Callable[[Exception], None]) -> None:
        self._on_auth_required_callback = callback

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
        self._shutdown_requested = False
        self._client = MaxClient()
        try:
            await self._client.connect()
            await login_with_token(self._client, self._session)
        except MaxAuthRequiredError as exc:
            await self._cleanup_failed_client()
            self._notify_auth_required(exc)
            raise
        except Exception:
            await self._cleanup_failed_client()
            raise
        self._connected = True

        self._client.set_reconnect_callback(self._request_reconnect)
        if self._packet_callback:
            self._client.set_packet_callback(self._packet_callback)

        logger.info("Connected and authenticated to MAX")
        return self._client

    def set_packet_callback(self, callback: PacketHandler) -> None:
        self._packet_callback = callback
        if self._client:
            self._client.set_packet_callback(callback)

    async def disconnect(self) -> None:
        self._shutdown_requested = True
        reconnect_task = self._reconnect_task
        self._reconnect_task = None
        if reconnect_task and not reconnect_task.done():
            reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reconnect_task

        if self._client:
            try:
                await self._client.disconnect()
            except Exception as e:
                logger.warning("Disconnect error: %s", e)
            self._client = None
            logger.info("Disconnected from MAX")
        self._connected = False
        self._reconnecting = False

    async def _cleanup_failed_client(self) -> None:
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                logger.debug("Disconnect error during auth cleanup", exc_info=True)
            self._client = None
        self._connected = False

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
            payload={"chatId": chat_id, "subscribe": False},
        )

    async def resolve_users(self, user_ids: list[int]) -> dict[str, Any]:
        return await self.client.invoke_method(
            opcode=Opcode.RESOLVE_USERS,
            payload={"contactIds": user_ids},
        )

    async def _request_reconnect(self) -> None:
        self.reconnect_nowait()

    def reconnect_nowait(self) -> bool:
        """Start reconnect loop in the background if one is not already running."""
        if self._shutdown_requested:
            return False
        if self._reconnect_task and not self._reconnect_task.done():
            return False

        task = asyncio.create_task(self._run_reconnect(), name="maxbridge-reconnect")
        self._reconnect_task = task
        task.add_done_callback(self._clear_reconnect_task)
        return True

    def _clear_reconnect_task(self, task: asyncio.Task) -> None:
        if self._reconnect_task is task:
            self._reconnect_task = None
        with contextlib.suppress(asyncio.CancelledError):
            exc = task.exception()
            if exc is not None:
                logger.exception("Reconnect task crashed", exc_info=exc)

    async def _run_reconnect(self) -> None:
        """Reconnect with exponential backoff. Inline client creation."""
        if self._shutdown_requested or self._reconnecting:
            return
        self._reconnecting = True
        self._connected = False

        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None

        try:
            for attempt in range(1, self._max_retries + 1):
                if self._shutdown_requested:
                    return

                delay = min(self._reconnect_delay * attempt, 60)
                logger.warning("Reconnecting in %ds (attempt %d/%d)...",
                               delay, attempt, self._max_retries)
                await asyncio.sleep(delay)
                if self._shutdown_requested:
                    return

                client: MaxClient | None = None
                try:
                    client = MaxClient()
                    await client.connect()
                    await login_with_token(client, self._session)

                    if self._shutdown_requested:
                        await client.disconnect()
                        return

                    self._client = client
                    self._connected = True
                    client.set_reconnect_callback(self._request_reconnect)
                    if self._packet_callback:
                        client.set_packet_callback(self._packet_callback)

                    logger.info("Reconnected on attempt %d", attempt)
                    return
                except MaxAuthRequiredError as exc:
                    if client is not None:
                        try:
                            await client.disconnect()
                        except Exception:
                            logger.debug("Disconnect error after auth failure", exc_info=True)
                    self._notify_auth_required(exc)
                    logger.warning("Reconnect stopped: authentication required")
                    return
                except Exception as exc:
                    logger.warning(
                        "Reconnect attempt %d/%d failed: %s: %s",
                        attempt,
                        self._max_retries,
                        exc.__class__.__name__,
                        exc,
                    )

            logger.critical("Failed to reconnect after %d attempts", self._max_retries)
            if self._on_fatal_callback:
                self._on_fatal_callback()
        finally:
            self._reconnecting = False

    def _notify_auth_required(self, exc: Exception) -> None:
        if self._on_auth_required_callback is None:
            return
        try:
            self._on_auth_required_callback(exc)
        except Exception:
            logger.exception("Auth-required callback failed")
