"""Shared type definitions for maxBridge."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from maxbridge.protocol.max_client import MaxClient


class MessageStatus(Enum):
    NEW = "message_new"
    EDITED = "message_edited"
    DELETED = "message_deleted"


# Canonical type alias — used by connection.py, event_router.py, handlers
PacketHandler = Callable[[MaxClient, dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass
class LinkedMessage:
    """Normalized linked/quoted/forwarded message payload."""
    message_id: str = ""
    sender_id: int | None = None
    text: str = ""
    timestamp: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict. Omits raw to prevent data leakage."""
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "text": self.text,
            "timestamp": self.timestamp,
            "attachments": self.attachments,
        }


@dataclass
class UnifiedMessage:
    """Platform-agnostic message representation for bridge consumers."""
    account_id: str
    chat_id: int
    message_id: str
    status: MessageStatus
    text: str = ""
    sender_id: int | None = None
    sender_name: str | None = None
    chat_name: str | None = None
    chat_type: str | None = None
    timestamp: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    link_type: str | None = None
    link_chat_id: int | None = None
    linked_message: LinkedMessage | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict. Omits raw to prevent data leakage."""
        return {
            "account_id": self.account_id,
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "status": self.status.value,
            "text": self.text,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "chat_name": self.chat_name,
            "chat_type": self.chat_type,
            "timestamp": self.timestamp,
            "attachments": self.attachments,
            "link_type": self.link_type,
            "link_chat_id": self.link_chat_id,
            "linked_message": (
                self.linked_message.to_dict()
                if self.linked_message is not None else None
            ),
        }
