"""Runtime statistics collector for maxBridge daemon."""

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorRecord:
    """Single error event."""
    timestamp: float
    kind: str
    account_id: str
    method: str
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "kind": self.kind,
            "account_id": self.account_id,
            "method": self.method,
            "error": self.error,
        }


class StatsCollector:
    """Collects runtime metrics: message counts, errors, latency, uptime."""

    def __init__(self, max_errors: int = 100) -> None:
        self._start_time = time.monotonic()
        self._start_epoch = time.time()

        # Message counters per account
        self._messages_received: dict[str, int] = {}
        self._messages_delivered: dict[str, int] = {}
        self._messages_dropped: dict[str, int] = {}

        # Per-method call counters
        self._rpc_calls: dict[str, int] = {}
        self._rpc_errors: dict[str, int] = {}
        self._log_errors = 0

        # Delayed attachment reconciliation counters
        self._attachment_notifications: dict[str, int] = {}
        self._attachment_reconciled: dict[str, int] = {}
        self._attachment_unresolved: dict[str, int] = {}

        # Error log (ring buffer)
        self._errors: deque[ErrorRecord] = deque(maxlen=max_errors)

        # Connection counters
        self._ipc_connections_total = 0
        self._ipc_connections_rejected = 0

    def record_message_received(self, account_id: str) -> None:
        self._messages_received[account_id] = (
            self._messages_received.get(account_id, 0) + 1
        )

    def record_message_delivered(self, account_id: str) -> None:
        self._messages_delivered[account_id] = (
            self._messages_delivered.get(account_id, 0) + 1
        )

    def record_message_dropped(self, account_id: str) -> None:
        self._messages_dropped[account_id] = (
            self._messages_dropped.get(account_id, 0) + 1
        )

    def record_rpc_call(self, method: str) -> None:
        self._rpc_calls[method] = self._rpc_calls.get(method, 0) + 1

    def record_rpc_error(self, method: str, account_id: str, error: str) -> None:
        self._rpc_errors[method] = self._rpc_errors.get(method, 0) + 1
        self._append_error("rpc", method, account_id, error)

    def record_log_error(self, logger_name: str, error: str,
                         account_id: str = "") -> None:
        self._log_errors += 1
        self._append_error("log", logger_name, account_id, error)

    def record_attachment_notification(self, account_id: str) -> None:
        self._attachment_notifications[account_id] = (
            self._attachment_notifications.get(account_id, 0) + 1
        )

    def record_attachment_reconciled(self, account_id: str) -> None:
        self._attachment_reconciled[account_id] = (
            self._attachment_reconciled.get(account_id, 0) + 1
        )

    def record_attachment_unresolved(self, account_id: str, detail: str) -> None:
        self._attachment_unresolved[account_id] = (
            self._attachment_unresolved.get(account_id, 0) + 1
        )
        self._append_error("attachment", "reconcile", account_id, detail)

    def _append_error(self, kind: str, method: str,
                      account_id: str, error: str) -> None:
        self._errors.append(ErrorRecord(
            timestamp=time.time(),
            kind=kind,
            account_id=account_id,
            method=method,
            error=error,
        ))

    def record_ipc_connection(self) -> None:
        self._ipc_connections_total += 1

    def record_ipc_rejection(self) -> None:
        self._ipc_connections_rejected += 1

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def get_stats(self) -> dict[str, Any]:
        """Full statistics snapshot."""
        return {
            "uptime_seconds": round(self.uptime_seconds, 1),
            "started_at": self._start_epoch,
            "messages": {
                "received": dict(self._messages_received),
                "delivered": dict(self._messages_delivered),
                "dropped": dict(self._messages_dropped),
                "total_received": sum(self._messages_received.values()),
                "total_delivered": sum(self._messages_delivered.values()),
                "total_dropped": sum(self._messages_dropped.values()),
            },
            "rpc": {
                "calls": dict(self._rpc_calls),
                "errors": dict(self._rpc_errors),
                "total_calls": sum(self._rpc_calls.values()),
                "total_errors": sum(self._rpc_errors.values()),
            },
            "attachments": {
                "notifications": dict(self._attachment_notifications),
                "reconciled": dict(self._attachment_reconciled),
                "unresolved": dict(self._attachment_unresolved),
                "total_notifications": sum(self._attachment_notifications.values()),
                "total_reconciled": sum(self._attachment_reconciled.values()),
                "total_unresolved": sum(self._attachment_unresolved.values()),
            },
            "errors": {
                "rpc_total": sum(self._rpc_errors.values()),
                "log_total": self._log_errors,
                "buffered": len(self._errors),
            },
            "connections": {
                "ipc_total": self._ipc_connections_total,
                "ipc_rejected": self._ipc_connections_rejected,
            },
        }

    def get_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent errors, newest first."""
        errors = list(self._errors)
        errors.reverse()
        return [e.to_dict() for e in errors[:limit]]

    def get_health(self, accounts_status: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Health check: overall system health."""
        connected_count = sum(
            1 for s in accounts_status.values() if s.get("connected")
        )
        total_count = len(accounts_status)
        error_rate = (
            sum(self._rpc_errors.values()) / max(sum(self._rpc_calls.values()), 1)
        )

        if total_count == 0:
            health = "no_accounts"
        elif connected_count == total_count:
            health = "healthy"
        elif connected_count > 0:
            health = "degraded"
        else:
            health = "unhealthy"

        return {
            "status": health,
            "accounts_connected": connected_count,
            "accounts_total": total_count,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "error_rate": round(error_rate, 4),
            "recent_errors": len(self._errors),
        }


class StatsLogHandler(logging.Handler):
    """Record runtime log errors into StatsCollector."""

    def __init__(self, stats: StatsCollector,
                 level: int = logging.ERROR) -> None:
        super().__init__(level)
        self._stats = stats

    def emit(self, record: logging.LogRecord) -> None:
        try:
            rendered = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._stats.record_log_error(record.name, rendered)
