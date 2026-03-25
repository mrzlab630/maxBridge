"""Тесты TUI хелперов."""

import os

from maxbridge.tui.helpers import (
    format_phone,
    format_ts,
    mask_device_id,
    resolve_session_path,
    session_exists,
)


class TestFormatTs:
    def test_valid_timestamp(self):
        # 1711234567890 ms = 2024-03-23 ...
        result = format_ts(1711234567890)
        assert "2024" in result
        assert "MSK" in result

    def test_none(self):
        assert format_ts(None) == "-"

    def test_zero(self):
        assert format_ts(0) == "-"

    def test_invalid(self):
        result = format_ts("not_a_number")
        assert result == "not_a_number"


class TestFormatPhone:
    def test_with_plus(self):
        assert format_phone("+79030605090") == "+79030605090"

    def test_without_plus(self):
        assert format_phone("79030605090") == "+79030605090"

    def test_int(self):
        assert format_phone(79030605090) == "+79030605090"


class TestMaskDeviceId:
    def test_normal(self):
        result = mask_device_id("abcdef12-3456-7890-abcd-ef1234567890")
        assert result == "abcd...7890"

    def test_short(self):
        assert mask_device_id("abc") == "abc"

    def test_none(self):
        assert mask_device_id("") == "?"
        assert mask_device_id(None) == "?"


class TestResolveSessionPath:
    def test_from_config(self):
        path = resolve_session_path("work", {"session_file": "/tmp/w.session"})
        assert path == "/tmp/w.session"

    def test_default(self):
        path = resolve_session_path("myacc", {})
        assert path == "data/myacc.session"


class TestSessionExists:
    def test_exists(self, tmp_dir):
        path = os.path.join(tmp_dir, "test.session")
        with open(path, "w") as f:
            f.write("data")
        assert session_exists("x", {"session_file": path}) is True

    def test_not_exists(self, tmp_dir):
        path = os.path.join(tmp_dir, "nope.session")
        assert session_exists("x", {"session_file": path}) is False

    def test_empty_file(self, tmp_dir):
        path = os.path.join(tmp_dir, "empty.session")
        open(path, "w").close()
        assert session_exists("x", {"session_file": path}) is False
