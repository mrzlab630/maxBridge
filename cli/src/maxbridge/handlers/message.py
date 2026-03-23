"""Handle incoming message events (opcode 128) with entity resolution."""

import logging
from typing import Any, Callable

from maxbridge.bridge.event_bus import EventBus
from maxbridge.bridge.transformer import packet_to_unified
from maxbridge.cache.entity_cache import EntityCache
from maxbridge.client.connection import MaxConnection
from maxbridge.ipc.stats import StatsCollector
from maxbridge.protocol.max_client import MaxClient
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.handlers.message")


def create_message_handler(
    event_bus: EventBus,
    connection: MaxConnection,
    account_id: str = "default",
    listen_chats: Any = "all",
    stats: StatsCollector | None = None,
    entity_cache: EntityCache | None = None,
) -> PacketHandler:
    """Factory: create handler with entity resolution and stats."""
    chat_filter = _build_chat_filter(listen_chats)

    async def handle_message(client: MaxClient, packet: dict[str, Any]) -> None:
        payload = packet.get("payload", {})
        chat_id = payload.get("chatId")

        if not chat_filter(chat_id):
            return

        unified = packet_to_unified(packet, account_id=account_id)
        if unified is None:
            return

        if stats:
            stats.record_message_received(account_id)

        # Enrich with cached entity data (lazy resolve)
        if entity_cache and connection.is_connected:
            await _enrich_message(connection, entity_cache, unified)

        logger.debug(
            "[%s][%s] chat=%d(%s) sender=%s(%s) len=%d",
            account_id, unified.status.value,
            unified.chat_id, unified.chat_name or "?",
            unified.sender_id, unified.sender_name or "?",
            len(unified.text) if unified.text else 0,
        )

        if event_bus.subscriber_count > 0:
            await event_bus.publish(unified)
            if stats:
                stats.record_message_delivered(account_id)
        else:
            if stats:
                stats.record_message_dropped(account_id)

    return handle_message


async def _enrich_message(conn: MaxConnection, cache: EntityCache,
                          unified: Any) -> None:
    """Populate sender_name, chat_name, chat_type from cache."""
    if unified.sender_id:
        user = await cache.resolve_user(conn, unified.sender_id)
        if user:
            unified.sender_name = user.name

    chat = await cache.resolve_chat(conn, unified.chat_id)
    if chat:
        unified.chat_name = chat.name
        unified.chat_type = chat.chat_type


def _build_chat_filter(listen_chats: Any) -> Callable[[int | None], bool]:
    if listen_chats == "all":
        return lambda _chat_id: True
    if isinstance(listen_chats, list):
        allowed = set(listen_chats)
        return lambda chat_id: chat_id in allowed
    logger.warning("Invalid listen_chats: %s — defaulting to 'all'", listen_chats)
    return lambda _chat_id: True
