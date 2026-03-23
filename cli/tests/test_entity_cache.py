"""Tests for entity cache with LRU eviction."""

import time
from unittest.mock import AsyncMock

import pytest

from maxbridge.cache.entity_cache import EntityCache


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.resolve_users = AsyncMock(return_value={
        "payload": {
            "contacts": [
                {"userId": 1, "firstName": "Alice", "lastName": "Smith", "nick": "asmith"},
            ]
        }
    })
    conn.resolve_chat = AsyncMock(return_value={
        "payload": {
            "chats": [
                {"chatId": 100, "title": "Test Group", "chatType": "GROUP"},
            ]
        }
    })
    return conn


class TestEntityCache:
    @pytest.mark.asyncio
    async def test_resolve_user_cache_miss(self, mock_conn):
        cache = EntityCache(ttl=600)
        user = await cache.resolve_user(mock_conn, 1)
        assert user is not None
        assert user.name == "Alice Smith"
        assert user.nick == "asmith"
        mock_conn.resolve_users.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_user_cache_hit(self, mock_conn):
        cache = EntityCache(ttl=600)
        await cache.resolve_user(mock_conn, 1)
        await cache.resolve_user(mock_conn, 1)
        # Should only call API once
        assert mock_conn.resolve_users.call_count == 1

    @pytest.mark.asyncio
    async def test_resolve_user_expired(self, mock_conn):
        cache = EntityCache(ttl=0)  # instant expiry
        await cache.resolve_user(mock_conn, 1)
        time.sleep(0.01)
        await cache.resolve_user(mock_conn, 1)
        assert mock_conn.resolve_users.call_count == 2

    @pytest.mark.asyncio
    async def test_resolve_chat(self, mock_conn):
        cache = EntityCache()
        chat = await cache.resolve_chat(mock_conn, 100)
        assert chat is not None
        assert chat.name == "Test Group"
        assert chat.chat_type == "GROUP"

    @pytest.mark.asyncio
    async def test_lru_eviction_users(self, mock_conn):
        cache = EntityCache(ttl=600, max_users=2)
        for uid in [1, 2, 3]:
            mock_conn.resolve_users.return_value = {
                "payload": {"contacts": [{"userId": uid, "firstName": f"U{uid}"}]}
            }
            await cache.resolve_user(mock_conn, uid)
        assert cache.user_count == 2  # oldest (1) evicted
        assert 1 not in cache._users

    @pytest.mark.asyncio
    async def test_lru_eviction_chats(self, mock_conn):
        cache = EntityCache(ttl=600, max_chats=2)
        for cid in [10, 20, 30]:
            mock_conn.resolve_chat.return_value = {
                "payload": {"chats": [{"chatId": cid, "title": f"C{cid}"}]}
            }
            await cache.resolve_chat(mock_conn, cid)
        assert cache.chat_count == 2

    @pytest.mark.asyncio
    async def test_resolve_user_api_failure(self, mock_conn):
        mock_conn.resolve_users.side_effect = Exception("network error")
        cache = EntityCache()
        user = await cache.resolve_user(mock_conn, 999)
        assert user is None  # no stale cache

    @pytest.mark.asyncio
    async def test_resolve_user_returns_stale_on_failure(self, mock_conn):
        cache = EntityCache(ttl=0)
        await cache.resolve_user(mock_conn, 1)  # populate
        time.sleep(0.01)
        mock_conn.resolve_users.side_effect = Exception("fail")
        user = await cache.resolve_user(mock_conn, 1)
        assert user is not None  # returns stale
        assert user.name == "Alice Smith"

    def test_parse_user_fallback_to_nick(self):
        result = {"payload": {"contacts": [{"userId": 5, "nick": "coolnick"}]}}
        user = EntityCache._parse_user(5, result)
        assert user.name == "coolnick"

    def test_parse_user_fallback_to_id(self):
        result = {"payload": {"contacts": [{"userId": 5}]}}
        user = EntityCache._parse_user(5, result)
        assert user.name == "5"

    def test_parse_chat_direct_payload(self):
        result = {"payload": {"title": "Direct Chat", "chatType": "DM"}}
        chat = EntityCache._parse_chat(1, result)
        assert chat.name == "Direct Chat"
        assert chat.chat_type == "DM"
