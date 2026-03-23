"""Native MAX WebSocket client — full replacement for vkmax.

Handles: connection with proper headers, packet send/receive with seq matching,
keepalive, hello, SMS auth, token auth, reconnect callback.
"""

import asyncio
import itertools
import json
import logging
import uuid
from typing import Any, Callable, Coroutine

import websockets

from maxbridge.protocol.errors import MaxApiError, MaxConnectionError, raise_for_payload

logger = logging.getLogger("maxbridge.protocol.max_client")

WS_HOST = "wss://ws-api.oneme.ru/websocket"
WS_ORIGIN = "https://web.max.ru"
WS_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
RPC_VERSION = 11
APP_VERSION = "26.2.2"
KEEPALIVE_INTERVAL = 30
KEEPALIVE_TIMEOUT = 15

PacketCallback = Callable[["MaxClient", dict[str, Any]], Coroutine[Any, Any, None]]
ReconnectCallback = Callable[[], Coroutine[Any, Any, None]]


class MaxClient:
    """Async MAX messenger WebSocket client."""

    def __init__(self) -> None:
        self._connection = None
        self._seq = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._packet_callback: PacketCallback | None = None
        self._reconnect_callback: ReconnectCallback | None = None
        self._device_id: str | None = None
        self._is_logged_in = False

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def is_connected(self) -> bool:
        return self._connection is not None

    # ── Connection ──────────────────────────────────────────

    async def connect(self) -> None:
        """Open WebSocket with proper headers. Starts receive loop."""
        if self._connection:
            raise RuntimeError("Already connected")

        self._connection = await websockets.connect(
            WS_HOST,
            additional_headers={
                "Origin": WS_ORIGIN,
                "User-Agent": WS_USER_AGENT,
            },
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.debug("Connected to %s", WS_HOST)

    async def disconnect(self) -> None:
        """Gracefully close everything."""
        self._is_logged_in = False
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            self._recv_task = None
        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                pass
            self._connection = None
        # Resolve any pending futures with error
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("Disconnected"))
        self._pending.clear()

    def set_packet_callback(self, callback: PacketCallback) -> None:
        """Set callback for incoming server-push packets."""
        self._packet_callback = callback

    def set_reconnect_callback(self, callback: ReconnectCallback) -> None:
        """Set callback invoked when connection drops."""
        self._reconnect_callback = callback

    # ── RPC ─────────────────────────────────────────────────

    async def invoke_method(self, opcode: int, payload: dict[str, Any],
                            timeout: float = 30.0) -> dict[str, Any]:
        """Send a packet and await the response (matched by seq)."""
        if not self._connection:
            raise RuntimeError("Not connected")

        seq = next(self._seq)
        request = {
            "ver": RPC_VERSION,
            "cmd": 0,
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
        }
        logger.debug("-> opcode=%d seq=%d", opcode, seq)

        future = asyncio.get_running_loop().create_future()
        self._pending[seq] = future

        await self._connection.send(json.dumps(request))

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise
        resp_payload = response.get("payload") or {}
        logger.debug("<- opcode=%d seq=%d keys=%s", opcode, seq,
                      list(resp_payload.keys()))
        return response

    # ── Auth ────────────────────────────────────────────────

    async def send_hello(self, device_id: str | None = None,
                         check_phone_auth: bool = False) -> dict[str, Any]:
        """Send device registration packet (opcode 6)."""
        self._device_id = device_id or str(uuid.uuid4())
        response = await self.invoke_method(
            opcode=6,
            payload={
                "userAgent": {
                    "deviceType": "WEB",
                    "locale": "ru_RU",
                    "osVersion": "Linux",
                    "deviceName": "maxBridge",
                    "headerUserAgent": WS_USER_AGENT,
                    "deviceLocale": "ru-RU",
                    "appVersion": APP_VERSION,
                    "screen": "1920x1080 1.0x",
                    "timezone": "Europe/Moscow",
                },
                "deviceId": self._device_id,
            },
        )
        payload = response.get("payload", {})
        location = payload.get("location", "unknown")
        auth_enabled = payload.get("phone-auth-enabled", True)
        logger.debug("Hello OK: location=%s, phone_auth=%s", location, auth_enabled)

        if check_phone_auth and not auth_enabled:
            raise MaxConnectionError(
                f"Phone auth disabled for your location ({location}). "
                f"Allowed countries: {', '.join(payload.get('reg-country-code', []))}. "
                f"Use QR auth instead (--auth-only) or use a VPN."
            )
        return response

    async def request_sms_code(self, phone: str) -> str:
        """Send SMS code to phone. Returns sms_token for verify_sms_code()."""
        await self.send_hello(check_phone_auth=True)

        response = await self.invoke_method(
            opcode=17,
            payload={"phone": phone, "type": "START_AUTH", "language": "ru"},
        )
        payload = response.get("payload", {})
        raise_for_payload(payload, context="SMS code request")

        token = (
            payload.get("token")
            or payload.get("smsToken")
            or payload.get("authToken")
            or payload.get("requestId")
        )
        if not token:
            raise MaxApiError(
                "unknown", "MAX returned success but no SMS token",
                details=f"Response keys: {list(payload.keys())}",
            )
        return token

    async def verify_sms_code(self, sms_token: str, code: int) -> dict[str, Any]:
        """Verify SMS code. Returns full response with profile and login token."""
        response = await self.invoke_method(
            opcode=18,
            payload={
                "token": sms_token,
                "verifyCode": str(code),
                "authTokenType": "CHECK_CODE",
            },
        )
        raise_for_payload(response.get("payload", {}), context="SMS verification")

        self._is_logged_in = True
        self._start_keepalive()
        return response

    async def login_by_token(self, token: str,
                             device_id: str | None = None) -> dict[str, Any]:
        """Authenticate with a saved login token."""
        await self.send_hello(device_id)

        response = await self.invoke_method(
            opcode=19,
            payload={
                "interactive": True,
                "token": token,
                "chatsSync": 0,
                "contactsSync": 0,
                "presenceSync": -1,
                "draftsSync": 0,
                "chatsCount": 40,
            },
        )
        raise_for_payload(response.get("payload", {}), context="Token login")

        self._is_logged_in = True
        self._start_keepalive()
        return response

    def extract_login_token(self, auth_response: dict[str, Any]) -> str:
        """Extract the persistent login token from SMS verify / token login response."""
        payload = auth_response.get("payload", {})

        # Path 1: tokenAttrs.LOGIN.token
        token_attrs = payload.get("tokenAttrs", {})
        if isinstance(token_attrs, dict):
            login = token_attrs.get("LOGIN", {})
            if isinstance(login, dict) and login.get("token"):
                return login["token"]

        # Path 2: direct token field
        for key in ("token", "loginToken", "authToken"):
            if payload.get(key):
                return payload[key]

        raise RuntimeError(
            f"Cannot extract login token. Keys: {list(payload.keys())}"
        )

    # ── QR Auth ─────────────────────────────────────────────

    async def request_qr(self) -> dict[str, Any]:
        """Request a QR code for auth. Returns qrLink, trackId, ttl."""
        await self.send_hello()
        response = await self.invoke_method(opcode=288, payload={})
        payload = response.get("payload", {})
        raise_for_payload(payload, context="QR request")
        return payload

    async def check_qr_status(self, track_id: str) -> dict[str, Any]:
        """Poll QR scan status. Returns status dict with possible token."""
        response = await self.invoke_method(
            opcode=289, payload={"trackId": track_id},
        )
        return response.get("payload", {})

    async def login_by_qr(self, track_id: str) -> dict[str, Any]:
        """Complete QR login after user scanned. Returns session response."""
        response = await self.invoke_method(
            opcode=291, payload={"trackId": track_id},
        )
        payload = response.get("payload", {})
        raise_for_payload(payload, context="QR login")
        self._is_logged_in = True
        self._start_keepalive()
        return response

    # ── Keepalive ───────────────────────────────────────────

    def _start_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """Periodic keepalive ping."""
        try:
            while self._is_logged_in and self._connection:
                try:
                    await asyncio.wait_for(
                        self.invoke_method(1, {"interactive": False}),
                        timeout=KEEPALIVE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning("Keepalive timeout")
                    if self._reconnect_callback:
                        asyncio.create_task(self._reconnect_callback())
                    return
                except Exception as e:
                    logger.warning("Keepalive error: %s", e)
                    break
                await asyncio.sleep(KEEPALIVE_INTERVAL)
        except asyncio.CancelledError:
            pass

    # ── Receive loop ────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """Process incoming WebSocket packets."""
        try:
            async for raw in self._connection:
                try:
                    packet = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                op = packet.get("opcode", "?")
                seq = packet.get("seq", 0)
                cmd = packet.get("cmd", "?")
                logger.debug("<< RECV cmd=%s op=%s seq=%s", cmd, op, seq)

                future = self._pending.pop(seq, None)

                if future and not future.done():
                    future.set_result(packet)
                elif self._packet_callback:
                    asyncio.create_task(
                        self._safe_callback(packet)
                    )
                else:
                    logger.debug("<< unhandled op=%s seq=%s", op, seq)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed")
            if self._reconnect_callback:
                asyncio.create_task(self._reconnect_callback())
        except asyncio.CancelledError:
            pass

    async def _safe_callback(self, packet: dict[str, Any]) -> None:
        """Call packet callback with error protection."""
        try:
            await self._packet_callback(self, packet)
        except Exception:
            logger.exception("Packet callback error")
