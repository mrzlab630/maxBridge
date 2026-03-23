"""Tests for EventBus with subscription filters."""

import pytest

from maxbridge.bridge.event_bus import EventBus, SubscriptionFilter
from maxbridge.utils.types import MessageStatus, UnifiedMessage


def _make_msg(account_id="a", chat_id=1):
    return UnifiedMessage(
        account_id=account_id, chat_id=chat_id,
        message_id="m1", status=MessageStatus.NEW, text="hi",
    )


class TestSubscriptionFilter:
    def test_no_filter_matches_all(self):
        f = SubscriptionFilter()
        assert f.matches(_make_msg("a", 1))
        assert f.matches(_make_msg("b", 2))

    def test_account_filter(self):
        f = SubscriptionFilter(account_ids={"alice"})
        assert f.matches(_make_msg("alice", 1))
        assert not f.matches(_make_msg("bob", 1))

    def test_chat_filter(self):
        f = SubscriptionFilter(chat_ids={10, 20})
        assert f.matches(_make_msg("a", 10))
        assert f.matches(_make_msg("b", 20))
        assert not f.matches(_make_msg("a", 30))

    def test_combined_filter(self):
        f = SubscriptionFilter(account_ids={"alice"}, chat_ids={10})
        assert f.matches(_make_msg("alice", 10))
        assert not f.matches(_make_msg("alice", 20))
        assert not f.matches(_make_msg("bob", 10))


class TestEventBus:
    @pytest.mark.asyncio
    async def test_publish_to_subscriber(self):
        bus = EventBus()
        received = []
        bus.subscribe("s1", lambda m: _append(received, m))
        await bus.publish(_make_msg())
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_filtered_publish(self):
        bus = EventBus()
        received = []
        filt = bus.make_filter(account_ids=["alice"])
        bus.subscribe("s1", lambda m: _append(received, m), filt)

        await bus.publish(_make_msg("alice", 1))
        await bus.publish(_make_msg("bob", 1))
        assert len(received) == 1
        assert received[0].account_id == "alice"

    @pytest.mark.asyncio
    async def test_no_subscribers_no_error(self):
        bus = EventBus()
        await bus.publish(_make_msg())  # should not raise

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        bus.subscribe("s1", lambda m: None)
        assert bus.subscriber_count == 1
        bus.unsubscribe("s1")
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_replace_subscriber(self):
        bus = EventBus()
        r1, r2 = [], []
        bus.subscribe("s1", lambda m: _append(r1, m))
        bus.subscribe("s1", lambda m: _append(r2, m))
        await bus.publish(_make_msg())
        assert len(r1) == 0
        assert len(r2) == 1


async def _append(lst, msg):
    lst.append(msg)
