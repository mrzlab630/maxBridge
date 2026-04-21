"""Tests for MAX QR authentication flow."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.auth.qr_auth import QrAuthSession, complete_qr_auth, render_qr_png
from maxbridge.auth.session import Session


class TestQrAuth:
    def test_render_qr_png_returns_png_bytes(self):
        png = render_qr_png("https://example.com/qr")

        assert png is not None
        assert png.startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_complete_qr_auth_saves_session_from_direct_token(self, tmp_dir, encryptor):
        session = Session(os.path.join(tmp_dir, "qr.session"), encryptor)
        qr_session = QrAuthSession(
            qr_link="https://example.com/qr",
            track_id="track-1",
            ttl=120,
            poll_interval=5,
        )

        client = MagicMock()
        client.device_id = "dev123"
        client.login_by_qr = AsyncMock(return_value={
            "payload": {"tokenAttrs": {"LOGIN": {"token": "tok123"}}},
        })
        client.extract_password_challenge.return_value = None
        client.extract_login_token.return_value = "tok123"
        client.check_password = AsyncMock()

        result = await complete_qr_auth(client, qr_session, session)

        assert result["payload"]["tokenAttrs"]["LOGIN"]["token"] == "tok123"
        assert session.exists()
        assert session.device_id == "dev123"
        assert session.token == "tok123"
        client.check_password.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_qr_auth_handles_password_challenge(self, tmp_dir, encryptor):
        session = Session(os.path.join(tmp_dir, "qr-challenge.session"), encryptor)
        qr_session = QrAuthSession(
            qr_link="https://example.com/qr",
            track_id="track-2",
            ttl=120,
            poll_interval=5,
        )
        challenge = {"trackId": "challenge-1", "hint": "pet", "email": "st***@mail.test"}

        client = MagicMock()
        client.device_id = "dev456"
        client.login_by_qr = AsyncMock(return_value={
            "payload": {"passwordChallenge": challenge},
        })
        client.extract_password_challenge.return_value = challenge
        client.check_password = AsyncMock(return_value={
            "payload": {"tokenAttrs": {"LOGIN": {"token": "tok456"}}},
        })
        client.extract_login_token.return_value = "tok456"
        password_provider = AsyncMock(return_value="super-secret")

        result = await complete_qr_auth(
            client,
            qr_session,
            session,
            password_provider=password_provider,
        )

        password_provider.assert_awaited_once_with(challenge)
        client.check_password.assert_awaited_once_with("challenge-1", "super-secret")
        assert result["payload"]["tokenAttrs"]["LOGIN"]["token"] == "tok456"
        assert session.exists()
        assert session.device_id == "dev456"
        assert session.token == "tok456"
