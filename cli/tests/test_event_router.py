"""Tests for event router."""

from unittest.mock import AsyncMock

import pytest

from maxbridge.client.event_router import EventRouter


class TestEventRouter:
    @pytest.mark.asyncio
    async def test_dispatch_to_opcode_handler(self):
        router = EventRouter()
        handler = AsyncMock()
        router.on_opcode(128, handler)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 128, "payload": {}})
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_ignores_unregistered_opcode(self):
        router = EventRouter()
        handler = AsyncMock()
        router.on_opcode(128, handler)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 999})
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_global_handler(self):
        router = EventRouter()
        global_h = AsyncMock()
        router.on_any(global_h)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 42})
        global_h.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_both_global_and_opcode(self):
        router = EventRouter()
        global_h = AsyncMock()
        opcode_h = AsyncMock()
        router.on_any(global_h)
        router.on_opcode(128, opcode_h)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 128})
        global_h.assert_called_once()
        opcode_h.assert_called_once()

    @pytest.mark.asyncio
    async def test_handler_error_does_not_crash(self):
        router = EventRouter()
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        router.on_opcode(128, handler)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 128})  # should not raise

    @pytest.mark.asyncio
    async def test_multiple_handlers_same_opcode(self):
        router = EventRouter()
        h1 = AsyncMock()
        h2 = AsyncMock()
        router.on_opcode(128, h1)
        router.on_opcode(128, h2)

        client = AsyncMock()
        await router.dispatch(client, {"opcode": 128})
        h1.assert_called_once()
        h2.assert_called_once()
