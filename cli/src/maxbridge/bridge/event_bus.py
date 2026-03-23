"""Internal pub/sub event bus with subscription filters."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

from maxbridge.utils.types import UnifiedMessage

logger = logging.getLogger("maxbridge.bridge.event_bus")

Subscriber = Callable[[UnifiedMessage], Coroutine[Any, Any, None]]


@dataclass
class SubscriptionFilter:
    """Filter for selective subscription."""
    account_ids: set[str] | None = None
    chat_ids: set[int] | None = None

    def matches(self, message: UnifiedMessage) -> bool:
        if self.account_ids and message.account_id not in self.account_ids:
            return False
        if self.chat_ids and message.chat_id not in self.chat_ids:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_ids": sorted(self.account_ids) if self.account_ids else None,
            "chat_ids": sorted(self.chat_ids) if self.chat_ids else None,
        }


class EventBus:
    """Async pub/sub bus with per-subscriber filters."""

    def __init__(self) -> None:
        self._subscribers: dict[str, tuple[Subscriber, SubscriptionFilter]] = {}

    @staticmethod
    def make_filter(account_ids: Any = None, chat_ids: Any = None) -> SubscriptionFilter:
        """Create a SubscriptionFilter from raw params."""
        aid = set(account_ids) if isinstance(account_ids, list) and account_ids else None
        cid = set(chat_ids) if isinstance(chat_ids, list) and chat_ids else None
        return SubscriptionFilter(account_ids=aid, chat_ids=cid)

    def subscribe(self, subscriber_id: str, callback: Subscriber,
                  filt: SubscriptionFilter | None = None) -> None:
        """Add subscriber with optional filter. Replaces existing."""
        self._subscribers[subscriber_id] = (callback, filt or SubscriptionFilter())
        logger.info("Subscriber added: %s filter=%s (total: %d)",
                     subscriber_id,
                     filt.to_dict() if filt else "all",
                     len(self._subscribers))

    def unsubscribe(self, subscriber_id: str) -> bool:
        removed = self._subscribers.pop(subscriber_id, None) is not None
        if removed:
            logger.info("Subscriber removed: %s (total: %d)",
                        subscriber_id, len(self._subscribers))
        return removed

    def has_subscriber(self, subscriber_id: str) -> bool:
        """Check if a subscriber exists."""
        return subscriber_id in self._subscribers

    def get_filter(self, subscriber_id: str) -> SubscriptionFilter | None:
        """Get filter for a subscriber."""
        entry = self._subscribers.get(subscriber_id)
        return entry[1] if entry else None

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, message: UnifiedMessage) -> None:
        """Publish to matching subscribers only."""
        if not self._subscribers:
            return
        tasks = []
        for sub_id, (callback, filt) in list(self._subscribers.items()):
            if filt.matches(message):
                tasks.append(self._safe_deliver(sub_id, callback, message))
        if tasks:
            await asyncio.gather(*tasks)

    @staticmethod
    async def _safe_deliver(sub_id: str, callback: Subscriber,
                            message: UnifiedMessage) -> None:
        try:
            await callback(message)
        except Exception:
            logger.exception("Delivery to subscriber %s failed", sub_id)
