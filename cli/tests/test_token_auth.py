"""Tests for token-based MAX authentication."""

import asyncio
import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.auth.session import Session
from maxbridge.auth.token_auth import login_with_token
from maxbridge.protocol.errors import MaxApiError, MaxAuthRequiredError


class TestTokenAuth:
    @pytest.mark.asyncio
    async def test_successful_login_backfills_encrypted_identity(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "legacy.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")
        client = MagicMock()
        client.login_by_token = AsyncMock(return_value={
            "payload": {"profile": {"contact": {"id": 987654}}},
        })

        await login_with_token(client, session)

        loaded = Session(path, encryptor)
        assert loaded.load() is True
        assert loaded.device_id == "dev123"
        assert loaded.token == "tok123"
        assert loaded.max_contact_id == "987654"

    @pytest.mark.asyncio
    async def test_login_without_verified_identity_does_not_backfill(
        self, tmp_dir, encryptor
    ):
        path = os.path.join(tmp_dir, "unknown-identity.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")
        before = open(path, "rb").read()
        client = MagicMock()
        client.login_by_token = AsyncMock(return_value={"payload": {}})

        await login_with_token(client, session)

        assert session.max_contact_id is None
        assert open(path, "rb").read() == before

    @pytest.mark.asyncio
    async def test_invalid_token_clears_session_and_raises_auth_required(self, tmp_dir,
                                                                         encryptor):
        path = os.path.join(tmp_dir, "expired.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")

        client = MagicMock()
        client.login_by_token = AsyncMock(side_effect=RuntimeError("token expired"))

        with pytest.raises(MaxAuthRequiredError):
            await login_with_token(client, session)

        assert not session.exists()
        assert session.device_id is None
        assert session.token is None

    @pytest.mark.asyncio
    async def test_transient_error_preserves_session(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "transient.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")

        client = MagicMock()
        client.login_by_token = AsyncMock(side_effect=RuntimeError("temporary websocket issue"))

        with pytest.raises(RuntimeError, match="transient error"):
            await login_with_token(client, session)

        assert session.exists()
        assert session.device_id == "dev123"
        assert session.token == "tok123"

    @pytest.mark.asyncio
    async def test_transient_timeout_logs_warning_without_error(self, tmp_dir,
                                                               encryptor,
                                                               caplog):
        path = os.path.join(tmp_dir, "timeout.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")

        client = MagicMock()
        client.login_by_token = AsyncMock(side_effect=asyncio.TimeoutError())

        with caplog.at_level(logging.WARNING, logger="maxbridge.auth.token"):
            with pytest.raises(RuntimeError, match="transient error"):
                await login_with_token(client, session)

        assert session.exists()
        assert "transient error" in caplog.text
        assert "TimeoutError" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    @pytest.mark.asyncio
    async def test_max_api_login_token_error_requires_reauth(self, tmp_dir, encryptor):
        path = os.path.join(tmp_dir, "login-token.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")

        client = MagicMock()
        client.login_by_token = AsyncMock(
            side_effect=MaxApiError("login.token", "Session token invalid"),
        )

        with pytest.raises(MaxAuthRequiredError):
            await login_with_token(client, session)

        assert not session.exists()

    @pytest.mark.asyncio
    async def test_identity_migration_does_not_clear_rejected_legacy_session(
        self, tmp_dir, encryptor
    ):
        path = os.path.join(tmp_dir, "preserved-rejected.session")
        session = Session(path, encryptor)
        session.save("dev123", "tok123")
        before = open(path, "rb").read()
        client = MagicMock()
        client.login_by_token = AsyncMock(side_effect=RuntimeError("token expired"))

        with pytest.raises(MaxAuthRequiredError):
            await login_with_token(
                client,
                session,
                clear_session_on_rejection=False,
            )

        assert session.exists()
        assert open(path, "rb").read() == before
