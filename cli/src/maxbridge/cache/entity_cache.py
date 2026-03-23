"""Lazy TTL cache with LRU eviction for user and chat entity resolution."""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from maxbridge.client.connection import MaxConnection

logger = logging.getLogger("maxbridge.cache.entity")


@dataclass
class CachedUser:
    user_id: int
    name: str
    nick: str | None = None
    cached_at: float = 0.0


@dataclass
class CachedChat:
    chat_id: int
    name: str
    chat_type: str | None = None
    cached_at: float = 0.0


class EntityCache:
    """TTL + LRU-bounded cache for user profiles and chat metadata."""

    def __init__(self, ttl: int = 600, max_users: int = 10000,
                 max_chats: int = 5000) -> None:
        self._ttl = ttl
        self._max_users = max_users
        self._max_chats = max_chats
        self._users: OrderedDict[int, CachedUser] = OrderedDict()
        self._chats: OrderedDict[int, CachedChat] = OrderedDict()

    def _is_expired(self, cached_at: float) -> bool:
        return (time.monotonic() - cached_at) > self._ttl

    async def resolve_user(self, conn: MaxConnection,
                           user_id: int) -> CachedUser | None:
        """Get user name, resolve from MAX API if not cached."""
        cached = self._users.get(user_id)
        if cached and not self._is_expired(cached.cached_at):
            self._users.move_to_end(user_id)
            return cached

        try:
            result = await conn.resolve_users([user_id])
            user = self._parse_user(user_id, result)
            if user:
                self._put_user(user_id, user)
                return user
        except Exception:
            logger.debug("Failed to resolve user %d", user_id)
        return cached

    async def resolve_chat(self, conn: MaxConnection,
                           chat_id: int) -> CachedChat | None:
        """Get chat name/type, resolve from MAX API if not cached."""
        cached = self._chats.get(chat_id)
        if cached and not self._is_expired(cached.cached_at):
            self._chats.move_to_end(chat_id)
            return cached

        try:
            result = await conn.resolve_chat(chat_id)
            chat = self._parse_chat(chat_id, result)
            if chat:
                self._put_chat(chat_id, chat)
                return chat
        except Exception:
            logger.debug("Failed to resolve chat %d", chat_id)
        return cached

    def _put_user(self, user_id: int, user: CachedUser) -> None:
        """Insert user with LRU eviction."""
        self._users[user_id] = user
        self._users.move_to_end(user_id)
        while len(self._users) > self._max_users:
            self._users.popitem(last=False)

    def _put_chat(self, chat_id: int, chat: CachedChat) -> None:
        """Insert chat with LRU eviction."""
        self._chats[chat_id] = chat
        self._chats.move_to_end(chat_id)
        while len(self._chats) > self._max_chats:
            self._chats.popitem(last=False)

    @property
    def user_count(self) -> int:
        return len(self._users)

    @property
    def chat_count(self) -> int:
        return len(self._chats)

    @staticmethod
    def _parse_user(user_id: int, result: dict[str, Any]) -> CachedUser | None:
        payload = result.get("payload", {})
        contacts = payload.get("contacts", payload.get("users", []))
        for contact in contacts:
            cid = contact.get("userId") or contact.get("id")
            if cid == user_id:
                name = _extract_user_name(contact)
                nick = contact.get("nick") or contact.get("username")
                return CachedUser(
                    user_id=user_id,
                    name=name or nick or str(user_id),
                    nick=nick,
                    cached_at=time.monotonic(),
                )
        return None

    @staticmethod
    def _parse_chat(chat_id: int, result: dict[str, Any]) -> CachedChat | None:
        payload = result.get("payload", {})
        chats = payload.get("chats", [])
        if not chats:
            title = payload.get("title") or payload.get("name")
            chat_type = payload.get("chatType") or payload.get("type")
            if title or chat_type:
                return CachedChat(
                    chat_id=chat_id,
                    name=title or str(chat_id),
                    chat_type=chat_type,
                    cached_at=time.monotonic(),
                )
            return None
        for chat in chats:
            cid = chat.get("chatId") or chat.get("id")
            if cid == chat_id:
                chat_type = chat.get("chatType") or chat.get("type")
                title = chat.get("title") or chat.get("name")
                if not title and chat_type == "DIALOG":
                    title = "DM"
                return CachedChat(
                    chat_id=chat_id,
                    name=title or str(chat_id),
                    chat_type=chat_type,
                    cached_at=time.monotonic(),
                )
        return None


def _extract_user_name(contact: dict[str, Any]) -> str:
    """Extract display name from MAX contact — handles names[] array format.

    MAX format: {"names": [{"firstName": "X", "lastName": "Y", "type": "ONEME"}, ...]}
    Prefers ONEME type, falls back to first available.
    """
    names_list = contact.get("names", [])
    if names_list:
        # Prefer ONEME type
        for entry in names_list:
            if entry.get("type") == "ONEME":
                first = entry.get("firstName", "")
                last = entry.get("lastName", "")
                return f"{first} {last}".strip()
        # Fallback to first entry
        for entry in names_list:
            name = entry.get("name") or entry.get("firstName", "")
            if name:
                last = entry.get("lastName", "")
                return f"{name} {last}".strip() if last else name

    # Legacy flat format
    first = contact.get("firstName", "")
    last = contact.get("lastName", "")
    if first or last:
        return f"{first} {last}".strip()

    return ""
