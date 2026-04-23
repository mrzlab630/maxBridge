"""Transform raw MAX WebSocket packets into UnifiedMessage objects."""

import logging
from typing import Any

from maxbridge.utils.types import LinkedMessage, MessageStatus, UnifiedMessage

logger = logging.getLogger("maxbridge.bridge.transformer")


def packet_to_unified(packet: dict[str, Any],
                      account_id: str = "default") -> UnifiedMessage | None:
    """Convert a raw opcode-128 packet to a UnifiedMessage.

    Returns None if the packet is malformed or unrecognized.
    """
    payload = packet.get("payload")
    if not payload:
        return None

    chat_id = payload.get("chatId")
    message = payload.get("message")
    if chat_id is None or message is None:
        return None

    status = _extract_status(message)
    text = message.get("text")
    if not isinstance(text, str):
        text = ""
    message_id = str(message.get("id", ""))
    sender_id = _extract_sender_id(message)
    timestamp = _extract_timestamp(message)
    attachments = _extract_attachments(message)
    link_type, link_chat_id, linked_message = _extract_link(message)

    return UnifiedMessage(
        account_id=account_id,
        chat_id=chat_id,
        message_id=message_id,
        status=status,
        text=text,
        sender_id=sender_id,
        timestamp=timestamp,
        attachments=attachments,
        link_type=link_type,
        link_chat_id=link_chat_id,
        linked_message=linked_message,
        raw=packet,
    )


def _extract_status(message: dict[str, Any]) -> MessageStatus:
    """Determine message status from the raw message dict."""
    raw_status = message.get("status")
    if raw_status == "REMOVED":
        return MessageStatus.DELETED
    if raw_status == "EDITED":
        return MessageStatus.EDITED
    return MessageStatus.NEW


def _extract_sender_id(message: dict[str, Any]) -> int | None:
    """Extract sender user ID from the message payload."""
    sender = message.get("sender")
    if isinstance(sender, dict):
        return sender.get("userId")
    if isinstance(sender, int):
        return sender
    return None


def _extract_timestamp(message: dict[str, Any]) -> int | None:
    """Extract server timestamp, falling back to client cid when needed."""
    timestamp = message.get("time")
    if isinstance(timestamp, int):
        return timestamp
    cid = message.get("cid")
    if isinstance(cid, int):
        return cid
    return None


def _extract_link(message: dict[str, Any]) -> tuple[str | None, int | None, LinkedMessage | None]:
    """Extract normalized linked/quoted message payload."""
    link = message.get("link")
    if not isinstance(link, dict):
        return None, None, None

    link_type = link.get("type") if isinstance(link.get("type"), str) else None
    link_chat_id = link.get("chatId") if isinstance(link.get("chatId"), int) else None
    inner = link.get("message")
    if not isinstance(inner, dict):
        return link_type, link_chat_id, None

    linked_message = LinkedMessage(
        message_id=str(inner.get("id", "")),
        sender_id=_extract_sender_id(inner),
        text=inner.get("text") if isinstance(inner.get("text"), str) else "",
        timestamp=_extract_timestamp(inner),
        attachments=_extract_attachments(inner),
        raw=inner,
    )
    return link_type, link_chat_id, linked_message


def _extract_attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract and normalize attachment list."""
    attaches = message.get("attaches", [])
    if not isinstance(attaches, list):
        return []

    result = []
    for attach in attaches:
        if not isinstance(attach, dict):
            continue
        result.append({
            "type": attach.get("_type", "unknown"),
            "data": attach,
        })
    return result
