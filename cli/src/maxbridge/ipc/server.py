"""IPC server — Unix socket / TCP with JSON-RPC 2.0 protocol.

Security: SO_PEERCRED, shared secret auth, rate limiting, idle timeout, line size limit.
"""

import asyncio
import logging
import os
import secrets
import socket
import struct
from pathlib import Path
from typing import Any

from maxbridge.bridge.event_bus import EventBus
from maxbridge.ipc.methods import RpcMethods
from maxbridge.ipc.protocol import (
    JsonRpcError,
    make_error,
    make_notification,
    make_response,
    parse_request,
)
from maxbridge.ipc.rate_limiter import RateLimiter
from maxbridge.ipc.stats import StatsCollector
from maxbridge.utils.constants import MAX_LINE_LENGTH
from maxbridge.utils.types import UnifiedMessage

logger = logging.getLogger("maxbridge.ipc.server")

_LINE_SEP = b"\n"
_IDLE_TIMEOUT = 300
_AUTH_ERROR_CODE = -32001
_RATE_LIMITED_CODE = -32000
_EXEMPT_FROM_AUTH = {"auth"}
_EXEMPT_FROM_RATE = {"ping", "health", "schema", "subscribe", "unsubscribe", "auth"}


class IpcServer:
    """JSON-RPC 2.0 server over Unix socket or TCP."""

    def __init__(self, methods: RpcMethods, event_bus: EventBus,
                 config: dict[str, Any], stats: StatsCollector | None = None) -> None:
        self._methods = methods
        self._bus = event_bus
        self._config = config
        self._stats = stats or StatsCollector()
        self._server: asyncio.AbstractServer | None = None
        self._clients: dict[str, asyncio.StreamWriter] = {}
        self._max_clients = config.get("max_clients", 10)
        self._auth_secret: str | None = config.get("auth_secret")
        self._authenticated: dict[str, bool] = {}
        self._rate_limiter = RateLimiter(config.get("rate_limits", {}))

    async def start(self) -> None:
        transport = self._config.get("transport", "unix")
        if transport == "unix":
            await self._start_unix()
        elif transport == "tcp":
            await self._start_tcp()
        else:
            raise ValueError(f"Unknown IPC transport: {transport}")

    async def _start_unix(self) -> None:
        path = self._config.get("unix_socket", self._default_socket_path())
        self._cleanup_socket(path)
        old_umask = os.umask(0o177)
        try:
            self._server = await asyncio.start_unix_server(
                self._handle_client, path=path, limit=MAX_LINE_LENGTH,
            )
        finally:
            os.umask(old_umask)
        logger.info("IPC server listening on unix:%s", path)

    async def _start_tcp(self) -> None:
        host = self._config.get("tcp_host", "127.0.0.1")
        port = self._config.get("tcp_port", 9100)
        if host not in ("127.0.0.1", "::1", "localhost"):
            if not self._config.get("allow_remote_tcp", False):
                raise ValueError(
                    f"Refusing to bind TCP to non-loopback {host}. "
                    "Set ipc.allow_remote_tcp: true to override."
                )
        if not self._auth_secret:
            logger.warning("TCP transport without auth_secret — any local process can connect!")
        self._server = await asyncio.start_server(
            self._handle_client, host=host, port=port, limit=MAX_LINE_LENGTH,
        )
        logger.info("IPC server listening on tcp:%s:%d", host, port)

    async def stop(self) -> None:
        for client_id, writer in list(self._clients.items()):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._clients.clear()
        self._authenticated.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        transport = self._config.get("transport", "unix")
        if transport == "unix":
            self._cleanup_socket(
                self._config.get("unix_socket", self._default_socket_path())
            )
        logger.info("IPC server stopped")

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername") or writer.get_extra_info("sockname")
        client_id = f"client_{id(writer)}"

        if not self._verify_peer(writer):
            self._stats.record_ipc_rejection()
            error = make_error(JsonRpcError.INTERNAL_ERROR, "Unauthorized")
            await self._send_line(writer, error)
            writer.close()
            await writer.wait_closed()
            return

        if len(self._clients) >= self._max_clients:
            self._stats.record_ipc_rejection()
            error = make_error(JsonRpcError.INTERNAL_ERROR, "Max clients reached")
            await self._send_line(writer, error)
            writer.close()
            await writer.wait_closed()
            return

        self._clients[client_id] = writer
        self._stats.record_ipc_connection()
        logger.info("Client connected: %s (%s)", client_id, peername)

        try:
            await self._read_loop(reader, writer, client_id)
        except (ConnectionResetError, BrokenPipeError):
            logger.info("Client disconnected: %s", client_id)
        except Exception:
            logger.exception("Error handling client %s", client_id)
        finally:
            if self._bus.has_subscriber(client_id):
                self._bus.unsubscribe(client_id)
            self._clients.pop(client_id, None)
            self._authenticated.pop(client_id, None)
            self._rate_limiter.remove_client(client_id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_loop(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter, client_id: str) -> None:
        while True:
            try:
                line = await asyncio.wait_for(
                    reader.readline(), timeout=_IDLE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.info("Client %s idle timeout", client_id)
                break
            except asyncio.LimitOverrunError:
                try:
                    await reader.readuntil(b"\n")
                except Exception:
                    pass
                await self._send_line(writer,
                                      make_error(JsonRpcError.PARSE_ERROR, "Request too large"))
                continue

            if not line:
                break

            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue

            method, error_msg, params, req_id = parse_request(raw)

            # Parse errors bypass auth — return immediately
            if error_msg:
                await self._send_line(writer, make_error(
                    JsonRpcError.PARSE_ERROR, error_msg, req_id))
                continue

            # Auth gate
            if self._auth_secret and not self._authenticated.get(client_id):
                if method not in _EXEMPT_FROM_AUTH:
                    await self._send_line(writer, make_error(
                        _AUTH_ERROR_CODE, "Authentication required", req_id))
                    continue
                if method == "auth":
                    await self._handle_auth(writer, client_id, params, req_id)
                    continue

            # Rate limit gate
            if method and method not in _EXEMPT_FROM_RATE:
                if not self._rate_limiter.allow(client_id, method):
                    self._stats.record_rpc_error(method, "", "Rate limit exceeded")
                    await self._send_line(writer, make_error(
                        _RATE_LIMITED_CODE, "Rate limit exceeded", req_id))
                    continue

            # Subscribe before response to avoid race
            if method == "subscribe":
                filt = self._bus.make_filter(
                    account_ids=params.get("account_ids"),
                    chat_ids=params.get("chat_ids"),
                )
                self._bus.subscribe(client_id, self._make_push_callback(writer), filt)
            elif method == "unsubscribe":
                self._bus.unsubscribe(client_id)

            response = await self._execute_parsed(method, error_msg, params, req_id)
            if response:
                await self._send_line(writer, response)

    async def _handle_auth(self, writer: asyncio.StreamWriter, client_id: str,
                           params: dict[str, Any], req_id: Any) -> None:
        """Validate auth secret from client."""
        token = params.get("secret", "")
        if not isinstance(token, str) or not secrets.compare_digest(token, self._auth_secret):
            self._stats.record_rpc_error("auth", "", "Invalid secret")
            await self._send_line(writer, make_error(
                _AUTH_ERROR_CODE, "Invalid secret", req_id))
            return
        self._authenticated[client_id] = True
        await self._send_line(writer, make_response({"authenticated": True}, req_id))
        logger.info("Client %s authenticated", client_id)

    async def _execute_parsed(self, method: str | None, error_msg: str | None,
                              params: dict[str, Any], req_id: Any) -> str:
        if error_msg:
            return make_error(JsonRpcError.PARSE_ERROR, error_msg, req_id)
        handler = self._methods.get(method)
        if handler is None:
            return make_error(JsonRpcError.METHOD_NOT_FOUND,
                              f"Unknown method: {method}", req_id)
        self._stats.record_rpc_call(method)
        try:
            valid_params = self._methods.filter_params(method, params)
            result = await handler(**valid_params)
            return make_response(result, req_id)
        except (ValueError, TypeError) as e:
            self._stats.record_rpc_error(method, "", str(e))
            return make_error(JsonRpcError.INVALID_PARAMS, str(e), req_id)
        except Exception:
            logger.exception("Method %s raised an error", method)
            self._stats.record_rpc_error(method, "", "Internal server error")
            return make_error(JsonRpcError.INTERNAL_ERROR, "Internal server error", req_id)

    @staticmethod
    def _verify_peer(writer: asyncio.StreamWriter) -> bool:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return True
        try:
            peercred = sock.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
            )
            _pid, uid, _gid = struct.unpack("3i", peercred)
            if uid != os.getuid():
                logger.warning("Rejected UID %d (expected %d)", uid, os.getuid())
                return False
        except (OSError, AttributeError):
            pass
        return True

    @staticmethod
    def _make_push_callback(writer: asyncio.StreamWriter):
        async def push(message: UnifiedMessage) -> None:
            notification = make_notification("message", message.to_dict())
            try:
                writer.write(notification.encode("utf-8") + _LINE_SEP)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                pass
        return push

    @staticmethod
    async def _send_line(writer: asyncio.StreamWriter, data: str) -> None:
        writer.write(data.encode("utf-8") + _LINE_SEP)
        await writer.drain()

    @staticmethod
    def _cleanup_socket(path: str) -> None:
        p = Path(path)
        if p.is_symlink():
            raise OSError(f"Socket path {path} is a symlink — refusing")
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _default_socket_path() -> str:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        return os.path.join(runtime_dir, "maxbridge.sock")
