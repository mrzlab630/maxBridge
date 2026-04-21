"""Tests for StatsCollector."""

import logging

from maxbridge.ipc.stats import StatsCollector, StatsLogHandler


class TestStatsCollector:
    def test_message_counters(self):
        s = StatsCollector()
        s.record_message_received("a")
        s.record_message_received("a")
        s.record_message_delivered("a")
        s.record_message_dropped("b")

        stats = s.get_stats()
        assert stats["messages"]["total_received"] == 2
        assert stats["messages"]["total_delivered"] == 1
        assert stats["messages"]["total_dropped"] == 1
        assert stats["messages"]["received"]["a"] == 2

    def test_rpc_counters(self):
        s = StatsCollector()
        s.record_rpc_call("ping")
        s.record_rpc_call("ping")
        s.record_rpc_call("send")
        s.record_rpc_error("send", "x", "fail")

        stats = s.get_stats()
        assert stats["rpc"]["total_calls"] == 3
        assert stats["rpc"]["total_errors"] == 1

    def test_errors_log(self):
        s = StatsCollector(max_errors=3)
        for i in range(5):
            s.record_rpc_error("m", "a", f"err{i}")
        errors = s.get_errors()
        assert len(errors) == 3
        assert errors[0]["error"] == "err4"  # newest first

    def test_health_healthy(self):
        s = StatsCollector()
        h = s.get_health({"a": {"connected": True}})
        assert h["status"] == "healthy"

    def test_health_degraded(self):
        s = StatsCollector()
        h = s.get_health({"a": {"connected": True}, "b": {"connected": False}})
        assert h["status"] == "degraded"

    def test_health_unhealthy(self):
        s = StatsCollector()
        h = s.get_health({"a": {"connected": False}})
        assert h["status"] == "unhealthy"

    def test_health_no_accounts(self):
        s = StatsCollector()
        h = s.get_health({})
        assert h["status"] == "no_accounts"

    def test_uptime(self):
        s = StatsCollector()
        assert s.uptime_seconds >= 0

    def test_connection_counters(self):
        s = StatsCollector()
        s.record_ipc_connection()
        s.record_ipc_rejection()
        stats = s.get_stats()
        assert stats["connections"]["ipc_total"] == 1
        assert stats["connections"]["ipc_rejected"] == 1

    def test_log_errors_are_buffered(self):
        s = StatsCollector()
        s.record_log_error("maxbridge.main", "startup failed")

        stats = s.get_stats()
        errors = s.get_errors()

        assert stats["errors"]["log_total"] == 1
        assert errors[0]["kind"] == "log"
        assert errors[0]["method"] == "maxbridge.main"

    def test_stats_log_handler_records_error_log(self):
        s = StatsCollector()
        handler = StatsLogHandler(s)
        handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        record = logging.LogRecord(
            name="maxbridge.client.connection",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="reconnect failed",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        errors = s.get_errors()
        assert errors[0]["kind"] == "log"
        assert errors[0]["error"] == "maxbridge.client.connection: reconnect failed"

    def test_attachment_counters_and_unresolved_buffer(self):
        s = StatsCollector()
        s.record_attachment_notification("default")
        s.record_attachment_reconciled("default")
        s.record_attachment_unresolved("default", "history miss")

        stats = s.get_stats()
        errors = s.get_errors()

        assert stats["attachments"]["total_notifications"] == 1
        assert stats["attachments"]["total_reconciled"] == 1
        assert stats["attachments"]["total_unresolved"] == 1
        assert errors[0]["kind"] == "attachment"
        assert errors[0]["method"] == "reconcile"
