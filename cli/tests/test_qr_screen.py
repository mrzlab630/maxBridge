"""TUI coverage for QR authentication challenges and errors."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import Input, Label

from maxbridge.auth.session import Session
from maxbridge.tui.screens.qr import QRScreen


def _qr_client(challenge: dict, password_result: dict | Exception) -> MagicMock:
    client = MagicMock()
    client.device_id = "device-2fa"
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.request_qr = AsyncMock(return_value={
        "qrLink": "https://example.com/qr",
        "trackId": "qr-track",
        "ttl": 1000,
        "pollingInterval": 1,
    })
    client.check_qr_status = AsyncMock(return_value={
        "status": {"loginAvailable": True},
    })
    client.login_by_qr = AsyncMock(return_value={
        "payload": {"passwordChallenge": challenge},
    })
    client.extract_password_challenge.return_value = challenge
    client.extract_login_token.return_value = "token-2fa"
    if isinstance(password_result, Exception):
        client.check_password = AsyncMock(side_effect=password_result)
    else:
        payload = password_result.setdefault("payload", {})
        payload.setdefault("profile", {"contact": {"id": 222}})
        client.check_password = AsyncMock(return_value=password_result)
    return client


@pytest.mark.asyncio
async def test_qr_screen_prompts_for_password_challenge(tmp_dir, encryptor):
    session = Session(os.path.join(tmp_dir, "qr-tui.session"), encryptor)
    challenge = {"trackId": "challenge-2fa", "hint": "pet", "email": "m***@mail.test"}
    client = _qr_client(challenge, {
        "payload": {"tokenAttrs": {"LOGIN": {"token": "token-2fa"}}},
    })
    screen = QRScreen(session, "account_2")
    app = App()

    with (
        patch("maxbridge.tui.screens.qr.MaxClient", return_value=client),
        patch("maxbridge.tui.screens.qr._SUCCESS_DISPLAY_SECONDS", 0),
    ):
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await pilot.pause(0.05)

            password_input = screen.query_one("#qr-password", Input)
            assert password_input.display is True
            assert password_input.region.y + password_input.region.height <= screen.region.height
            assert "пароль двухэтапной проверки" in str(
                screen.query_one("#qr-status-label", Label).render()
            )

            password_input.post_message(Input.Submitted(password_input, "secret-2fa"))
            await pilot.pause(0.05)

    client.check_password.assert_awaited_once_with("challenge-2fa", "secret-2fa")
    assert session.load() is True
    assert session.device_id == "device-2fa"
    assert session.token == "token-2fa"
    assert session.max_contact_id == "222"


@pytest.mark.asyncio
async def test_qr_screen_keeps_auth_error_visible(tmp_dir, encryptor):
    session = Session(os.path.join(tmp_dir, "qr-tui-error.session"), encryptor)
    challenge = {"trackId": "challenge-error"}
    client = _qr_client(challenge, RuntimeError("Неверный пароль MAX"))
    screen = QRScreen(session, "account_2")
    app = App()

    with patch("maxbridge.tui.screens.qr.MaxClient", return_value=client):
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await pilot.pause(0.05)

            password_input = screen.query_one("#qr-password", Input)
            password_input.post_message(Input.Submitted(password_input, "wrong-password"))
            await pilot.pause(0.05)

            assert app.screen is screen
            status = screen.query_one("#qr-status-label", Label)
            assert status.region.y + status.region.height <= screen.region.height
            assert "Неверный пароль MAX" in str(
                status.render()
            )
            assert session.exists() is False


@pytest.mark.asyncio
async def test_qr_screen_rejects_duplicate_and_clears_only_new_session(
    tmp_dir, encryptor
):
    session = Session(os.path.join(tmp_dir, "qr-duplicate.session"), encryptor)
    client = MagicMock()
    client.device_id = "new-device"
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.request_qr = AsyncMock(return_value={
        "qrLink": "https://example.com/qr",
        "trackId": "qr-track",
        "ttl": 1000,
        "pollingInterval": 1,
    })
    client.check_qr_status = AsyncMock(return_value={
        "status": {"loginAvailable": True},
    })
    client.login_by_qr = AsyncMock(return_value={
        "payload": {
            "token": "new-token",
            "profile": {"contact": {"id": 777}},
        },
    })
    client.extract_password_challenge.return_value = None
    client.extract_login_token.return_value = "new-token"
    duplicate_guard = AsyncMock(return_value="default")
    screen = QRScreen(session, "account_2", duplicate_guard=duplicate_guard)
    app = App()

    with patch("maxbridge.tui.screens.qr.MaxClient", return_value=client):
        async with app.run_test(size=(80, 24)) as pilot:
            await app.push_screen(screen)
            await pilot.pause(0.05)

            status = str(screen.query_one("#qr-status-label", Label).render())
            assert app.screen is screen
            assert "account_2" in status
            assert "default" in status
            assert "777" not in status
            assert "new-token" not in status

    duplicate_guard.assert_awaited_once_with("777")
    assert session.exists() is False
