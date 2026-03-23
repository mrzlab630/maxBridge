"""Route incoming WebSocket packets by opcode to registered handlers."""

import asyncio
import logging
from collections import defaultdict
from typing import Any

from maxbridge.protocol.max_client import MaxClient
from maxbridge.utils.types import PacketHandler

logger = logging.getLogger("maxbridge.client.event_router")


class EventRouter:
    """Dispatches incoming MAX packets to opcode-specific handlers."""

    def __init__(self) -> None:
        self._handlers: dict[int, list[PacketHandler]] = defaultdict(list)
        self._global_handlers: list[PacketHandler] = []

    def on_opcode(self, opcode: int, handler: PacketHandler) -> None:
        """Register a handler for a specific opcode."""
        self._handlers[opcode].append(handler)
        logger.debug("Registered handler for opcode %d: %s", opcode, handler.__name__)

    def on_any(self, handler: PacketHandler) -> None:
        """Register a handler that receives ALL packets."""
        self._global_handlers.append(handler)

    async def dispatch(self, client: MaxClient, packet: dict[str, Any]) -> None:
        """Route a packet to all matching handlers."""
        opcode = packet.get("opcode")

        tasks = [self._safe_call(h, client, packet) for h in self._global_handlers]

        if opcode in self._handlers:
            tasks.extend(self._safe_call(h, client, packet) for h in self._handlers[opcode])

        if tasks:
            await asyncio.gather(*tasks)

    @staticmethod
    async def _safe_call(handler: PacketHandler, client: MaxClient,
                         packet: dict[str, Any]) -> None:
        """Call a handler with error protection."""
        try:
            await handler(client, packet)
        except Exception:
            logger.exception("Handler %s raised an error", handler.__name__)
