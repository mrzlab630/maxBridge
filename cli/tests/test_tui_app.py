"""Regression tests for TUI daemon logging."""

import importlib.util
import os
import pathlib
import stat
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from maxbridge.tui.screens import telegram as telegram_screen

APP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src/maxbridge/tui/app.py"
APP_SPEC = importlib.util.spec_from_file_location("maxbridge_tui_app_under_test", APP_PATH)
assert APP_SPEC and APP_SPEC.loader
tui_app = importlib.util.module_from_spec(APP_SPEC)
APP_SPEC.loader.exec_module(tui_app)


def test_runtime_root_resolves_checkout_cli():
    assert tui_app._runtime_root() == APP_PATH.parents[3]


def test_daemon_log_is_checkout_local_append_only_and_private(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    log_path = tmp_path / "logs/maxbridge-tui-daemon.log"
    log_path.parent.mkdir()
    log_path.write_text("before\n", encoding="utf-8")
    os.chmod(log_path, 0o644)

    with tui_app._open_daemon_log() as daemon_log:
        daemon_log.write("after\n")

    assert tui_app._daemon_log_path() == log_path
    assert log_path.read_text(encoding="utf-8") == "before\nafter\n"
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"), reason="platform does not support O_NOFOLLOW"
)
def test_daemon_log_rejects_symlink_without_mutating_target(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    log_path = tmp_path / "logs/maxbridge-tui-daemon.log"
    target_path = tmp_path / "external-daemon.log"
    log_path.parent.mkdir()
    target_path.write_text("external\n", encoding="utf-8")
    os.chmod(target_path, 0o640)
    log_path.symlink_to(target_path)

    with pytest.raises(OSError):
        tui_app._open_daemon_log()

    assert target_path.read_text(encoding="utf-8") == "external\n"
    assert stat.S_IMODE(target_path.stat().st_mode) == 0o640


class _StoppedDaemonTUI:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str | None]] = []
        self.timer_calls: list[tuple[object, object]] = []

    def _daemon_pid(self):
        return None

    def notify(self, message, severity=None):
        self.notifications.append((message, severity))

    def set_timer(self, delay, callback):
        self.timer_calls.append((delay, callback))

    def _update_status(self):
        pass


class _RunningDaemonTUI(_StoppedDaemonTUI):
    def _daemon_pid(self):
        return 123


def test_tui_daemon_launch_uses_one_private_log_for_both_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    calls = []

    def fake_popen(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(tui_app.subprocess, "Popen", fake_popen)
    tui = _StoppedDaemonTUI()

    tui_app.MaxBridgeTUI._toggle_daemon(tui)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ([
        tui_app.sys.executable,
        "-m",
        "maxbridge.main",
        "-c",
        str(tmp_path / "config/local.yaml"),
    ],)
    assert kwargs["cwd"] == tmp_path
    assert kwargs["stdout"] is kwargs["stderr"]
    assert kwargs["stdout"] is not tui_app.subprocess.DEVNULL
    assert kwargs["stdout"].closed
    assert kwargs["start_new_session"] is True
    log_path = tmp_path / "logs/maxbridge-tui-daemon.log"
    assert log_path.is_file()
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600
    assert tui.notifications == [("🚀 Запуск демона...", None)]


def test_tui_entry_changes_to_runtime_root_before_loading_state(tmp_path, monkeypatch):
    events = []

    class FakeApp:
        def __init__(self):
            events.append("init")

        def run(self):
            events.append("run")

    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(tui_app.os, "chdir", lambda path: events.append(("chdir", path)))
    monkeypatch.setattr(tui_app, "MaxBridgeTUI", FakeApp)

    tui_app.tui_entry()

    assert events == [("chdir", tmp_path), "init", "run"]


def test_telegram_test_copy_states_direct_bot_api_scope_only():
    assert telegram_screen._DIRECT_TEST_LABEL == "🧪 Тест Bot API"
    assert "Только прямая проверка Bot API" in telegram_screen._DIRECT_TEST_SCOPE
    assert "готовность форвардинга демона не проверяется" in telegram_screen._DIRECT_TEST_SCOPE


@pytest.mark.asyncio
async def test_telegram_direct_test_success_logs_scope_disclaimer(monkeypatch):
    logs = []
    screen = SimpleNamespace(
        query_one=MagicMock(
            side_effect=[SimpleNamespace(value="token"), SimpleNamespace(value="chat")]
        ),
        _log=logs.append,
    )

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(telegram_screen.aiohttp, "ClientSession", lambda **_kwargs: Session())

    await telegram_screen.TelegramScreen._test.__wrapped__(screen)

    assert telegram_screen._DIRECT_TEST_SCOPE in logs
    assert any("не подтверждает готовность форвардинга демона" in line for line in logs)


def test_tui_reports_log_open_error_without_starting_daemon(monkeypatch):
    def fail_open():
        raise OSError("permission denied")

    monkeypatch.setattr(tui_app, "_open_daemon_log", fail_open)
    popen_called = False

    def fake_popen(*args, **kwargs):
        nonlocal popen_called
        popen_called = True

    monkeypatch.setattr(tui_app.subprocess, "Popen", fake_popen)
    tui = _StoppedDaemonTUI()

    tui_app.MaxBridgeTUI._toggle_daemon(tui)

    assert not popen_called
    assert tui.notifications == [
        ("❌ Не удалось открыть лог демона: permission denied", "error")
    ]
    assert tui.timer_calls == []


def test_tui_daemon_stop_behavior_does_not_open_log(monkeypatch):
    killed = []

    def fake_kill(pid, signal):
        killed.append((pid, signal))

    def fail_open():
        pytest.fail("stopping a daemon must not open its log")

    monkeypatch.setattr(tui_app.os, "kill", fake_kill)
    monkeypatch.setattr(tui_app, "_open_daemon_log", fail_open)
    tui = _RunningDaemonTUI()

    tui_app.MaxBridgeTUI._toggle_daemon(tui)

    assert killed == [(123, tui_app.signal.SIGTERM)]
    assert tui.notifications == [("🛑 Останавливаем демон (PID 123)...", None)]
