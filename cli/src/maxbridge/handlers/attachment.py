"""Handle delayed attachment notifications (opcode 136)."""

import logging
from typing import Any

from maxbridge.bridge.event_bus import EventBus
from maxbridge.bridge.transformer import packet_to_unified
from maxbridge.cache.entity_cache import EntityCache
from maxbridge.client.connection import MaxConnection
from maxbridge.handlers.message import _build_chat_filter, _enrich_message
from maxbridge.ipc.stats import StatsCollector
from maxbridge.protocol.max_client import MaxClient
from maxbridge.utils.types import PacketHandler, UnifiedMessage

logger = logging.getLogger("maxbridge.handlers.attachment")

_HISTORY_WINDOWS = (30, 100)


def create_upload_complete_handler(
    event_bus: EventBus,
    connection: MaxConnection,
    account_id: str = "default",
    listen_chats: Any = "all",
    stats: StatsCollector | None = None,
    entity_cache: EntityCache | None = None,
) -> PacketHandler:
    """Best-effort recovery for delayed attachments that arrive after opcode 128."""
    chat_filter = _build_chat_filter(listen_chats)

    async def handle_upload_complete(client: MaxClient, packet: dict[str, Any]) -> None:
        payload = packet.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}

        if stats:
            stats.record_attachment_notification(account_id)

        diagnostic = _summarize_payload(payload)
        logger.debug("[%s] Attachment notification received: %s", account_id, diagnostic)

        direct = packet_to_unified(packet, account_id=account_id)
        if direct is not None and direct.attachments and chat_filter(direct.chat_id):
            await _publish_unified(
                event_bus=event_bus,
                connection=connection,
                entity_cache=entity_cache,
                unified=direct,
            )
            if stats:
                stats.record_attachment_reconciled(account_id)
            logger.info(
                "[%s] Attachment notification published directly: chat=%s message=%s",
                account_id,
                direct.chat_id,
                direct.message_id or "?",
            )
            return

        chat_id, message_id = _extract_identifiers(payload)
        if chat_id is None:
            _mark_unresolved(
                stats=stats,
                account_id=account_id,
                detail=f"missing chatId; {diagnostic}",
            )
            logger.warning("[%s] Attachment notification missing chat id: %s", account_id, diagnostic)
            return

        if not chat_filter(chat_id):
            return

        if message_id is None:
            _mark_unresolved(
                stats=stats,
                account_id=account_id,
                detail=f"chatId={chat_id}; missing messageId; {diagnostic}",
            )
            logger.warning(
                "[%s] Attachment notification missing message id: chat=%s %s",
                account_id,
                chat_id,
                diagnostic,
            )
            return

        try:
            message = await _refetch_message(connection, chat_id, message_id)
        except Exception as exc:
            _mark_unresolved(
                stats=stats,
                account_id=account_id,
                detail=f"chatId={chat_id}; messageId={message_id}; history error: {exc}",
            )
            logger.exception(
                "[%s] Attachment notification history fetch failed: chat=%s message=%s",
                account_id,
                chat_id,
                message_id,
            )
            return

        if message is None:
            _mark_unresolved(
                stats=stats,
                account_id=account_id,
                detail=f"chatId={chat_id}; messageId={message_id}; history miss; {diagnostic}",
            )
            logger.warning(
                "[%s] Attachment notification could not be reconciled: chat=%s message=%s",
                account_id,
                chat_id,
                message_id,
            )
            return

        refetched_packet = {
            "payload": {
                "chatId": chat_id,
                "message": message,
            },
        }
        unified = packet_to_unified(refetched_packet, account_id=account_id)
        if unified is None or not unified.attachments:
            _mark_unresolved(
                stats=stats,
                account_id=account_id,
                detail=(
                    f"chatId={chat_id}; messageId={message_id}; "
                    "message found without attachments"
                ),
            )
            logger.warning(
                "[%s] Attachment notification resolved message without attachments: "
                "chat=%s message=%s",
                account_id,
                chat_id,
                message_id,
            )
            return

        await _publish_unified(
            event_bus=event_bus,
            connection=connection,
            entity_cache=entity_cache,
            unified=unified,
        )
        if stats:
            stats.record_attachment_reconciled(account_id)
        logger.info(
            "[%s] Attachment notification reconciled: chat=%s message=%s attachments=%d",
            account_id,
            chat_id,
            unified.message_id or message_id,
            len(unified.attachments),
        )

    return handle_upload_complete


async def _publish_unified(event_bus: EventBus, connection: MaxConnection,
                           entity_cache: EntityCache | None,
                           unified: UnifiedMessage) -> None:
    if entity_cache and connection.is_connected:
        await _enrich_message(connection, entity_cache, unified)
    if event_bus.subscriber_count > 0:
        await event_bus.publish(unified)


async def _refetch_message(connection: MaxConnection, chat_id: int,
                           message_id: str) -> dict[str, Any] | None:
    target = str(message_id)
    for count in _HISTORY_WINDOWS:
        result = await connection.get_chat_history(chat_id, count=count)
        payload = result.get("payload", {})
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")) == target:
                return item
    return None


def _extract_identifiers(payload: dict[str, Any]) -> tuple[int | None, str | None]:
    chat_id = _coerce_int(payload.get("chatId"))
    message_id = _coerce_message_id(payload.get("messageId"))

    message = payload.get("message")
    if isinstance(message, dict):
        if chat_id is None:
            chat_id = _coerce_int(message.get("chatId"))
        if message_id is None:
            message_id = _coerce_message_id(message.get("messageId") or message.get("id"))

    chat = payload.get("chat")
    if chat_id is None and isinstance(chat, dict):
        chat_id = _coerce_int(chat.get("chatId") or chat.get("id"))

    notification = payload.get("notification")
    if isinstance(notification, dict):
        if chat_id is None:
            chat_id = _coerce_int(notification.get("chatId"))
        if message_id is None:
            message_id = _coerce_message_id(
                notification.get("messageId") or notification.get("id"),
            )

    return chat_id, message_id


def _summarize_payload(payload: dict[str, Any]) -> str:
    keys = ",".join(sorted(payload.keys())[:8]) or "-"
    chat_id, message_id = _extract_identifiers(payload)
    parts = [f"keys={keys}"]
    if chat_id is not None:
        parts.append(f"chatId={chat_id}")
    if message_id is not None:
        parts.append(f"messageId={message_id}")

    for key in ("message", "chat", "notification", "attach", "attaches"):
        value = payload.get(key)
        if isinstance(value, dict):
            nested = ",".join(sorted(value.keys())[:6]) or "-"
            parts.append(f"{key}Keys={nested}")
        elif isinstance(value, list):
            parts.append(f"{key}Count={len(value)}")

    return "; ".join(parts)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _coerce_message_id(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _mark_unresolved(stats: StatsCollector | None, account_id: str, detail: str) -> None:
    if stats:
        stats.record_attachment_unresolved(account_id, detail)
