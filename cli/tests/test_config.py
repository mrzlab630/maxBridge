"""Tests for configuration loading."""

from maxbridge.config import _apply_env_overrides, _deep_merge, get_nested, load_config


class TestDeepMerge:
    def test_simple(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"x": {"a": 1, "b": 3, "c": 4}}


class TestGetNested:
    def test_simple(self):
        cfg = {"a": {"b": {"c": 42}}}
        assert get_nested(cfg, "a.b.c") == 42

    def test_default(self):
        assert get_nested({}, "a.b", "fallback") == "fallback"

    def test_partial_path(self):
        cfg = {"a": 1}
        assert get_nested(cfg, "a.b.c", "nope") == "nope"


class TestLoadConfig:
    def test_loads_defaults(self):
        cfg = load_config()
        assert "accounts" in cfg or "max" in cfg
        assert "ipc" in cfg
        assert "logging" in cfg


class TestEnvironmentOverrides:
    def test_daemon_pid_file_uses_exact_nested_mapping(self, monkeypatch):
        config = {"daemon": {"pid_file": "/tmp/original.pid"}}
        monkeypatch.setenv("MAXBRIDGE_DAEMON_PID_FILE", "/run/maxbridge/maxbridge.pid")

        _apply_env_overrides(config)

        assert config["daemon"]["pid_file"] == "/run/maxbridge/maxbridge.pid"
        assert "pid" not in config["daemon"]

    def test_missing_daemon_pid_override_preserves_config(self, monkeypatch):
        config = {"daemon": {"pid_file": "/tmp/original.pid"}}
        monkeypatch.delenv("MAXBRIDGE_DAEMON_PID_FILE", raising=False)

        _apply_env_overrides(config)

        assert config["daemon"]["pid_file"] == "/tmp/original.pid"

    def test_similar_unapproved_environment_name_is_ignored(self, monkeypatch):
        config = {"daemon": {"pid_file": "/tmp/original.pid"}}
        monkeypatch.setenv("MAXBRIDGE_DAEMON_PID_FILE_EXTRA", "/tmp/unapproved.pid")

        _apply_env_overrides(config)

        assert config == {"daemon": {"pid_file": "/tmp/original.pid"}}
