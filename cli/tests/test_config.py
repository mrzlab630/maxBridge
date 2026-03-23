"""Tests for configuration loading."""

from maxbridge.config import _deep_merge, get_nested, load_config


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
