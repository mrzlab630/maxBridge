"""CLI lifecycle separation for terminal-only authentication."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import maxbridge.auth.qr_auth as qr_auth_module
import maxbridge.main as main_module
from maxbridge.auth.qr_auth import QrAuthSession, complete_qr_auth


def _run_cli(
    monkeypatch,
    argv,
    *,
    auth_side_effect=None,
    daemon_side_effect=None,
    stop_side_effect=None,
):
    authenticator = MagicMock()
    authenticator.authenticate = AsyncMock(side_effect=auth_side_effect)
    daemon = MagicMock()
    daemon.start = AsyncMock(side_effect=daemon_side_effect)
    daemon.stop = AsyncMock(side_effect=stop_side_effect)
    daemon.request_shutdown = MagicMock()
    drain = AsyncMock()

    authenticator_cls = MagicMock(return_value=authenticator)
    daemon_cls = MagicMock(return_value=daemon)
    monkeypatch.setattr(main_module, "TerminalAuthenticator", authenticator_cls, raising=False)
    monkeypatch.setattr(main_module, "MaxBridgeDaemon", daemon_cls)
    monkeypatch.setattr(main_module, "load_config", lambda _path: {})
    monkeypatch.setattr(main_module, "setup_logging", MagicMock())
    monkeypatch.setattr(main_module, "_drain_background_tasks", drain)
    monkeypatch.setattr("sys.argv", ["maxbridge", *argv])

    main_module.cli_entry()
    return authenticator_cls, authenticator, daemon_cls, daemon, drain


@pytest.mark.parametrize("side_effect", [None, RuntimeError("auth failed"), KeyboardInterrupt()])
def test_auth_only_never_enters_daemon_lifecycle(monkeypatch, side_effect):
    authenticator_cls, authenticator, daemon_cls, daemon, drain = _run_cli(
        monkeypatch,
        ["--auth-only", "--account", "default"],
        auth_side_effect=side_effect,
    )

    authenticator_cls.assert_called_once_with({})
    authenticator.authenticate.assert_awaited_once_with("default")
    daemon_cls.assert_not_called()
    daemon.start.assert_not_awaited()
    daemon.stop.assert_not_awaited()
    drain.assert_not_awaited()


def test_normal_partial_start_runs_stop_and_drain_once(monkeypatch):
    _authenticator_cls, authenticator, daemon_cls, daemon, drain = _run_cli(
        monkeypatch,
        [],
        daemon_side_effect=RuntimeError("partial startup"),
    )

    daemon_cls.assert_called_once_with({})
    authenticator.authenticate.assert_not_awaited()
    daemon.start.assert_awaited_once()
    daemon.stop.assert_awaited_once()
    drain.assert_awaited_once()


def test_normal_drain_runs_when_stop_fails(monkeypatch):
    with pytest.raises(RuntimeError, match="stop failed"):
        _run_cli(monkeypatch, [], stop_side_effect=RuntimeError("stop failed"))


def test_runtime_status_nests_only_sanitized_control_polling_fields():
    daemon = main_module.MaxBridgeDaemon.__new__(main_module.MaxBridgeDaemon)
    daemon._shutdown_event = MagicMock()
    daemon._shutdown_event.is_set.return_value = False
    daemon._control_bot = MagicMock()
    daemon._control_bot.is_ready = False
    daemon._control_bot.polling_health = {
        "polling_enabled": False,
        "lease_held": False,
        "conflict_state": "external_conflict",
        "consecutive_conflicts": 1,
    }
    daemon._telegram = MagicMock()
    daemon._telegram.is_ready = True
    daemon._telegram_error_handler = None
    daemon._ipc_server = MagicMock()
    daemon._ipc_server.is_running = True
    daemon._ipc_server.client_count = 0
    daemon._event_bus = MagicMock()
    daemon._event_bus.subscriber_count = 0

    polling = daemon._runtime_status()["telegram"]["control_bot"]

    assert polling == daemon._control_bot.polling_health
    assert set(polling) == {
        "polling_enabled",
        "lease_held",
        "conflict_state",
        "consecutive_conflicts",
    }


@pytest.mark.asyncio
async def test_terminal_password_challenge_uses_getpass_without_disclosure(
    monkeypatch,
    capsys,
    caplog,
):
    password = "test-password-value"
    challenge = {"trackId": "test-track", "hint": "test-hint"}
    client = MagicMock()
    client.device_id = "test-device"
    client.login_by_qr = AsyncMock(return_value={"payload": {"passwordChallenge": challenge}})
    client.extract_password_challenge.return_value = challenge
    client.check_password = AsyncMock(return_value={"payload": {"token": "test-login-token"}})
    client.extract_login_token.return_value = "test-login-token"
    session = MagicMock()
    monkeypatch.setattr(qr_auth_module.getpass, "getpass", lambda _prompt: password)

    await complete_qr_auth(
        client,
        QrAuthSession("test-qr-link", "test-track", 1, 1),
        session,
    )

    client.check_password.assert_awaited_once_with("test-track", password)
    combined = capsys.readouterr().out + capsys.readouterr().err + caplog.text
    assert password not in combined
