"""Tests for StatsCollector."""

from maxbridge.ipc.stats import StatsCollector


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
