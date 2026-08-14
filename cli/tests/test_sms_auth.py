"""Tests for verified identity handling in SMS authentication."""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from maxbridge.auth.session import Session
from maxbridge.auth.sms_auth import verify_sms_code


@pytest.mark.asyncio
async def test_sms_auth_resolves_missing_profile_before_saving(tmp_dir, encryptor):
    session = Session(os.path.join(tmp_dir, "sms.session"), encryptor)
    client = MagicMock()
    client.device_id = "sms-device"
    client.verify_sms_code = AsyncMock(return_value={"payload": {"token": "sms-token"}})
    client.extract_login_token.return_value = "sms-token"
    client.login_by_token = AsyncMock(return_value={
        "payload": {"profile": {"contact": {"id": 321}}},
    })

    await verify_sms_code(client, "sms-challenge", 1234, session)

    client.login_by_token.assert_awaited_once_with("sms-token", "sms-device")
    assert session.max_contact_id == "321"
