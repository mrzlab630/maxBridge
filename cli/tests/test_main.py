"""Tests for maxBridge process shutdown helpers."""

import asyncio

import pytest

from maxbridge.main import _drain_background_tasks


class TestMainShutdown:
    @pytest.mark.asyncio
    async def test_drain_background_tasks_cancels_pending_tasks(self):
        loop = asyncio.get_running_loop()
        cancelled = asyncio.Event()

        async def background() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(background())
        await asyncio.sleep(0)

        await _drain_background_tasks(loop, shutdown_executor=False)

        assert cancelled.is_set()
        assert task.cancelled()
