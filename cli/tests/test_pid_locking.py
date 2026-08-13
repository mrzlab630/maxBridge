"""Owner-safe daemon PID file tests."""

import os
from pathlib import Path

import pytest

from maxbridge.main import MaxBridgeDaemon


def _daemon(pid_path: object) -> MaxBridgeDaemon:
    daemon = MaxBridgeDaemon.__new__(MaxBridgeDaemon)
    daemon._config = {"daemon": {"pid_file": pid_path}}
    daemon._pid_fd = None
    daemon._pid_identity = None
    daemon._pid_path = None
    return daemon


@pytest.mark.parametrize("value", [None, "", "   ", "relative/maxbridge.pid", 7])
def test_invalid_pid_path_fails_before_filesystem_mutation(tmp_path, monkeypatch, value):
    daemon = _daemon(value)
    open_calls = []
    mkdir_calls = []
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: open_calls.append(args))
    monkeypatch.setattr(Path, "mkdir", lambda *args, **kwargs: mkdir_calls.append(args))

    with pytest.raises(RuntimeError, match="absolute path"):
        daemon._write_pid()

    assert open_calls == []
    assert mkdir_calls == []
    assert list(tmp_path.iterdir()) == []


def test_losing_contender_does_not_truncate_owner_bytes(tmp_path):
    pid_path = tmp_path / "maxbridge.pid"
    owner = _daemon(str(pid_path))
    contender = _daemon(str(pid_path))
    owner._write_pid()
    owner_bytes = pid_path.read_bytes()

    try:
        with pytest.raises(RuntimeError, match="Another instance is running"):
            contender._write_pid()

        assert pid_path.read_bytes() == owner_bytes
        assert contender._pid_fd is None
        assert contender._pid_identity is None
    finally:
        owner._remove_pid()


def test_non_owner_cleanup_does_not_unlink_pid_path(tmp_path):
    pid_path = tmp_path / "maxbridge.pid"
    pid_path.write_text("owned elsewhere", encoding="utf-8")

    _daemon(str(pid_path))._remove_pid()

    assert pid_path.read_text(encoding="utf-8") == "owned elsewhere"


def test_owner_cleanup_preserves_replaced_inode_and_is_idempotent(tmp_path):
    pid_path = tmp_path / "maxbridge.pid"
    owner = _daemon(str(pid_path))
    owner._write_pid()
    original_identity = owner._pid_identity
    pid_path.unlink()
    pid_path.write_text("replacement", encoding="utf-8")

    owner._remove_pid()
    owner._remove_pid()

    assert original_identity is not None
    assert pid_path.read_text(encoding="utf-8") == "replacement"
    assert owner._pid_fd is None
    assert owner._pid_identity is None
    assert owner._pid_path is None


def test_owner_cleanup_unlinks_owned_inode(tmp_path):
    pid_path = tmp_path / "maxbridge.pid"
    owner = _daemon(str(pid_path))
    owner._write_pid()

    owner._remove_pid()

    assert not pid_path.exists()


def test_owner_cleanup_uses_path_retained_at_acquisition(tmp_path):
    owned_path = tmp_path / "owned.pid"
    replacement_config_path = tmp_path / "other.pid"
    replacement_config_path.write_text("other owner", encoding="utf-8")
    owner = _daemon(str(owned_path))
    owner._write_pid()
    owner._config["daemon"]["pid_file"] = str(replacement_config_path)

    owner._remove_pid()

    assert not owned_path.exists()
    assert replacement_config_path.read_text(encoding="utf-8") == "other owner"
