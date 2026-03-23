"""JSON-RPC methods — multi-account, media, ACK, filtered subscribe."""

import inspect
import logging
from typing import Any, Callable, Coroutine

from maxbridge.bridge.event_bus import EventBus
from maxbridge.client.account_manager import AccountManager
from maxbridge.ipc.schema import get_mcp_schema
from maxbridge.ipc.stats import StatsCollector
from maxbridge.media.uploader import get_download_url, upload_and_send_file, upload_and_send_photo
from maxbridge.utils.constants import MAX_HISTORY_COUNT, MAX_MESSAGE_LENGTH, MAX_USER_IDS

logger = logging.getLogger("maxbridge.ipc.methods")

RpcHandler = Callable[..., Coroutine[Any, Any, dict[str, Any]]]


class RpcMethods:
    """Registry of all JSON-RPC methods."""

    def __init__(self, manager: AccountManager, event_bus: EventBus,
                 stats: StatsCollector) -> None:
        self._manager = manager
        self._bus = event_bus
        self._stats = stats
        self._methods: dict[str, RpcHandler] = {
            # Monitoring
            "ping": self.ping,
            "health": self.health,
            "status": self.status,
            "accounts": self.accounts,
            "stats": self.stats,
            "errors": self.errors,
            "schema": self.schema,
            # Messaging
            "send_message": self.send_message,
            "send_photo": self.send_photo,
            "send_file": self.send_file,
            "get_history": self.get_history,
            "get_chat_info": self.get_chat_info,
            "get_user_info": self.get_user_info,
            "get_download_url": self.get_download_url,
            # Subscription
            "subscribe": self.subscribe,
            "unsubscribe": self.unsubscribe,
            "list_methods": self.list_methods,
        }
        self._signatures: dict[str, inspect.Signature] = {
            name: inspect.signature(fn) for name, fn in self._methods.items()
        }

    def get(self, method_name: str) -> RpcHandler | None:
        return self._methods.get(method_name)

    def filter_params(self, method_name: str, params: dict[str, Any]) -> dict[str, Any]:
        sig = self._signatures.get(method_name)
        if sig is None:
            return params
        return {k: v for k, v in params.items() if k in sig.parameters}

    # ── Monitoring ──────────────────────────────────────────

    async def ping(self, **_kw) -> dict[str, Any]:
        return {"pong": True}

    async def health(self, **_kw) -> dict[str, Any]:
        return self._stats.get_health(self._manager.status())

    async def status(self, **_kw) -> dict[str, Any]:
        return {
            "accounts": self._manager.status(),
            "subscribers": self._bus.subscriber_count,
        }

    async def accounts(self, **_kw) -> dict[str, Any]:
        return self._manager.status()

    async def stats(self, **_kw) -> dict[str, Any]:
        return self._stats.get_stats()

    async def errors(self, limit: Any = 50, **_kw) -> dict[str, Any]:
        if not isinstance(limit, int):
            limit = 50
        limit = min(max(1, limit), 100)
        return {"errors": self._stats.get_errors(limit)}

    async def schema(self, **_kw) -> dict[str, Any]:
        return get_mcp_schema()

    # ── Messaging ───────────────────────────────────────────

    async def send_message(self, account_id: Any = "", chat_id: Any = 0,
                           text: Any = "", **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int):
            raise TypeError("chat_id must be an integer")
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        if not chat_id or not text:
            raise ValueError("chat_id and text are required")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"text too long (max {MAX_MESSAGE_LENGTH} chars)")
        account = self._manager.require(account_id)
        result = await account.connection.send_message(chat_id, text)
        return {"sent": True, "account_id": account_id, "result": str(result)}

    async def send_photo(self, account_id: Any = "", chat_id: Any = 0,
                         file_path: Any = "", caption: Any = "", **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int) or not chat_id:
            raise ValueError("chat_id is required (integer)")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("file_path is required")
        account = self._manager.require(account_id)
        return await upload_and_send_photo(
            account.connection, chat_id, file_path, str(caption or ""))

    async def send_file(self, account_id: Any = "", chat_id: Any = 0,
                        file_path: Any = "", caption: Any = "", **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int) or not chat_id:
            raise ValueError("chat_id is required (integer)")
        if not isinstance(file_path, str) or not file_path:
            raise ValueError("file_path is required")
        account = self._manager.require(account_id)
        return await upload_and_send_file(
            account.connection, chat_id, file_path, str(caption or ""))

    async def get_history(self, account_id: Any = "", chat_id: Any = 0,
                          count: Any = 30, **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int) or not chat_id:
            raise ValueError("chat_id is required (integer)")
        if not isinstance(count, int):
            count = 30
        count = min(max(1, count), MAX_HISTORY_COUNT)
        account = self._manager.require(account_id)
        result = await account.connection.get_chat_history(chat_id, count)
        return result.get("payload", result)

    async def get_chat_info(self, account_id: Any = "",
                            chat_id: Any = 0, **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int) or not chat_id:
            raise ValueError("chat_id is required (integer)")
        account = self._manager.require(account_id)
        result = await account.connection.resolve_chat(chat_id)
        return result.get("payload", result)

    async def get_user_info(self, account_id: Any = "",
                            user_ids: Any = None, **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not user_ids:
            raise ValueError("user_ids is required")
        if not isinstance(user_ids, list) or not all(isinstance(x, int) for x in user_ids):
            raise TypeError("user_ids must be a list of integers")
        if len(user_ids) > MAX_USER_IDS:
            raise ValueError(f"Too many user_ids (max {MAX_USER_IDS})")
        account = self._manager.require(account_id)
        result = await account.connection.resolve_users(user_ids)
        return result.get("payload", result)

    async def get_download_url(self, account_id: Any = "", chat_id: Any = 0,
                               message_id: Any = "", file_id: Any = 0,
                               media_type: Any = "file", **_kw) -> dict[str, Any]:
        account_id = self._resolve_account_id(account_id)
        if not isinstance(chat_id, int) or not chat_id:
            raise ValueError("chat_id is required")
        if not message_id:
            raise ValueError("message_id is required")
        if not isinstance(file_id, int) or not file_id:
            raise ValueError("file_id is required")
        account = self._manager.require(account_id)
        url = await get_download_url(
            account.connection, chat_id, str(message_id), file_id, str(media_type))
        return {"url": url}

    # ── Subscription ──────────────────────────────────────

    async def subscribe(self, account_ids: Any = None,
                        chat_ids: Any = None, **_kw) -> dict[str, Any]:
        filt = self._bus.make_filter(account_ids, chat_ids)
        return {"subscribed": True, "filter": filt.to_dict()}

    async def unsubscribe(self, **_kw) -> dict[str, Any]:
        return {"unsubscribed": True}

    async def list_methods(self, **_kw) -> dict[str, Any]:
        return {"methods": list(self._methods.keys())}

    # ── Internal ────────────────────────────────────────────

    def _resolve_account_id(self, account_id: Any) -> str:
        if not account_id or not isinstance(account_id, str):
            ids = self._manager.account_ids
            if not ids:
                raise ValueError("No accounts configured")
            return ids[0]
        return account_id
