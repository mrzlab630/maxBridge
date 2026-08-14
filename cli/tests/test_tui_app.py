"""Regression tests for TUI daemon logging."""

import importlib.util
import os
import pathlib
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.tui.screens import chat_view as chat_view_screen
from maxbridge.tui.screens import qr as qr_screen
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
    monkeypatch.setattr(
        tui_app,
        "setup_error_logging",
        lambda path: events.append(("setup_error_logging", path)),
    )
    monkeypatch.setattr(tui_app, "MaxBridgeTUI", FakeApp)

    tui_app.tui_entry()

    assert events == [
        ("chdir", tmp_path),
        ("setup_error_logging", tmp_path),
        "init",
        "run",
    ]


def test_main_menu_has_logs_before_daemon_toggle():
    options = tui_app._main_menu_options()
    option_ids = [option.id for option in options]

    assert option_ids == [
        "sessions",
        "chats",
        "telegram",
        "logs",
        "daemon-toggle",
        "quit",
    ]
    assert str(options[3].prompt).startswith("📋 Логи")


def test_selecting_logs_dispatches_logs_action():
    fake = SimpleNamespace(
        _open_sessions=MagicMock(),
        _open_chats=MagicMock(),
        _open_telegram=MagicMock(),
        _open_logs=MagicMock(),
        _toggle_daemon=MagicMock(),
        exit=MagicMock(),
    )

    tui_app.MaxBridgeTUI._menu_select(
        fake, SimpleNamespace(option_id="logs")
    )

    fake._open_logs.assert_called_once_with()


def test_open_logs_uses_current_config_and_runtime_root(tmp_path, monkeypatch):
    pushed = []
    config = {"ipc": {"transport": "unix"}}
    fake = SimpleNamespace(_config=config, push_screen=pushed.append)
    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)

    tui_app.MaxBridgeTUI._open_logs(fake)

    assert len(pushed) == 1
    assert isinstance(pushed[0], tui_app.LogsScreen)
    assert pushed[0]._ipc_config == config["ipc"]
    assert pushed[0]._runtime_root == tmp_path


def test_daemon_status_updates_menu_option_by_id(monkeypatch):
    replacements = []
    option_list = SimpleNamespace(
        replace_option_prompt=lambda option_id, label: replacements.append(
            (option_id, label)
        )
    )
    fake = SimpleNamespace(
        _daemon_pid=lambda: None,
        _accounts_cfg={},
        _pulse_tick=0,
        _active_aid=None,
        query_one=lambda *_args: option_list,
        sub_title="",
    )
    monkeypatch.setattr(tui_app, "session_exists", lambda *_args: False)

    tui_app.MaxBridgeTUI._update_status(fake)

    assert replacements == [("daemon-toggle", "🟢 Запустить демон")]


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


def test_tui_daemon_stop_oserror_is_logged_once_and_shown_safely(monkeypatch):
    monkeypatch.setattr(
        tui_app.os,
        "kill",
        MagicMock(side_effect=PermissionError("accessToken=stop-secret")),
    )
    log_exception = MagicMock()
    monkeypatch.setattr(tui_app.logger, "exception", log_exception)
    tui = _RunningDaemonTUI()

    tui_app.MaxBridgeTUI._toggle_daemon(tui)

    log_exception.assert_called_once_with("Unable to stop the maxBridge daemon")
    assert tui.notifications == [
        ("❌ Не удалось остановить демон: accessToken=[REDACTED]", "error")
    ]
    assert tui.timer_calls == []


def test_tui_daemon_popen_oserror_is_logged_once_and_shown_safely(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(
        tui_app.subprocess,
        "Popen",
        MagicMock(side_effect=OSError("clientSecret=start-secret")),
    )
    log_exception = MagicMock()
    monkeypatch.setattr(tui_app.logger, "exception", log_exception)
    tui = _StoppedDaemonTUI()

    tui_app.MaxBridgeTUI._toggle_daemon(tui)

    log_exception.assert_called_once_with("Unable to start the maxBridge daemon")
    assert tui.notifications == [
        ("❌ Не удалось запустить демон: clientSecret=[REDACTED]", "error")
    ]
    assert tui.timer_calls == []


@pytest.mark.parametrize("failure_stage", ["init", "run"])
def test_tui_entry_logs_top_level_failures_after_sink_setup(
    failure_stage, tmp_path, monkeypatch
):
    events = []

    class FailingApp:
        def __init__(self):
            events.append("init")
            if failure_stage == "init":
                raise RuntimeError("refreshToken=init-secret")

        def run(self):
            events.append("run")
            raise RuntimeError("refreshToken=run-secret")

    monkeypatch.setattr(tui_app, "_runtime_root", lambda: tmp_path)
    monkeypatch.setattr(tui_app.os, "chdir", lambda path: events.append(("chdir", path)))
    monkeypatch.setattr(
        tui_app,
        "setup_error_logging",
        lambda path: events.append(("setup_error_logging", path)),
    )
    monkeypatch.setattr(tui_app, "MaxBridgeTUI", FailingApp)
    log_exception = MagicMock()
    monkeypatch.setattr(tui_app.logger, "exception", log_exception)

    with pytest.raises(RuntimeError):
        tui_app.tui_entry()

    log_exception.assert_called_once_with("maxBridge TUI startup or runtime failed")
    assert events[:2] == [
        ("chdir", tmp_path),
        ("setup_error_logging", tmp_path),
    ]


def test_telegram_save_failure_is_logged_once_and_shown_without_secret(monkeypatch):
    config = SimpleNamespace(bot_token="", chat_id="", enabled=True)
    logs = []
    notifications = []
    screen = SimpleNamespace(
        _config=config,
        query_one=MagicMock(
            side_effect=[
                SimpleNamespace(value="botToken=save-secret"),
                SimpleNamespace(value="chat"),
            ]
        ),
        _log=logs.append,
        notify=lambda message, severity=None: notifications.append((message, severity)),
    )
    monkeypatch.setattr(
        telegram_screen,
        "save_telegram_config",
        MagicMock(side_effect=OSError("access_token=save-secret")),
    )
    log_exception = MagicMock()
    monkeypatch.setattr(telegram_screen.logger, "exception", log_exception)

    saved = telegram_screen.TelegramScreen._save(screen)

    assert saved is False
    log_exception.assert_called_once_with("Failed to save Telegram settings")
    assert logs == ["[red]❌ Не удалось сохранить настройки (OSError)[/red]"]
    assert notifications == [
        ("❌ Не удалось сохранить настройки (OSError)", "error")
    ]


@pytest.mark.asyncio
async def test_telegram_non_2xx_is_logged_once_with_redacted_bounded_body(monkeypatch):
    secret = "clientSecret=response-secret"
    logs = []
    screen = SimpleNamespace(
        query_one=MagicMock(
            side_effect=[SimpleNamespace(value="token"), SimpleNamespace(value="chat")]
        ),
        _log=logs.append,
    )

    class Response:
        status = 401

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def text(self):
            return secret + "x" * 600

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(
        telegram_screen.aiohttp, "ClientSession", lambda **_kwargs: Session()
    )
    log_error = MagicMock()
    monkeypatch.setattr(telegram_screen.logger, "error", log_error)

    await telegram_screen.TelegramScreen._test.__wrapped__(screen)

    log_error.assert_called_once()
    logged_body = log_error.call_args.args[2]
    assert logged_body.startswith("clientSecret=[REDACTED]")
    assert len(logged_body) <= 500
    assert "response-secret" not in logged_body
    assert "response-secret" not in "\n".join(logs)


@pytest.mark.asyncio
async def test_qr_protocol_error_is_logged_once_before_safe_display(monkeypatch):
    events = []

    class Client:
        async def connect(self):
            return None

        async def check_qr_status(self, _track_id):
            return {"error": "accessToken=qr-secret"}

        async def disconnect(self):
            return None

    session = SimpleNamespace(
        qr_link="https://example.invalid/qr",
        ttl=0.01,
        poll_interval=0.01,
        track_id="track",
    )
    log_widget = SimpleNamespace(write=lambda _text: None)
    status_widget = SimpleNamespace(update=lambda _text: None)
    screen = SimpleNamespace(
        _aid="account",
        _session=object(),
        query_one=MagicMock(side_effect=[log_widget, status_widget]),
        _show_error=lambda message: events.append(("display", message)),
    )

    async def fake_request_qr_session(_client):
        return session

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(qr_screen, "MaxClient", Client)
    monkeypatch.setattr(qr_screen, "request_qr_session", fake_request_qr_session)
    monkeypatch.setattr(qr_screen.asyncio, "sleep", no_sleep)
    log_error = MagicMock(
        side_effect=lambda *_args, **_kwargs: events.append(("log", _args))
    )
    monkeypatch.setattr(qr_screen.logger, "error", log_error)

    await qr_screen.QRScreen._do_auth.__wrapped__(screen)

    log_error.assert_called_once()
    assert events[0][0] == "log"
    assert events[1] == ("display", "QR отклонён: accessToken=[REDACTED]")
    assert "qr-secret" not in str(log_error.call_args)


@pytest.mark.asyncio
async def test_max_send_payload_error_is_logged_once_and_shown_safely(monkeypatch):
    client = SimpleNamespace(
        invoke_method=AsyncMock(
            return_value={"payload": {"error": "clientSecret=send-secret"}}
        )
    )
    errors = []
    screen = SimpleNamespace(
        _client=client,
        _chat_id=123,
        _aid="account",
        _my_id=456,
        _write_error=errors.append,
        _write_msg=MagicMock(),
    )
    log_error = MagicMock()
    monkeypatch.setattr(chat_view_screen.logger, "error", log_error)

    await chat_view_screen.ChatViewScreen._do_send_async(screen, "hello")

    log_error.assert_called_once_with(
        "MAX send response error for account '%s': %s",
        "account",
        "clientSecret=[REDACTED]",
    )
    assert errors == ["clientSecret=[REDACTED]"]
