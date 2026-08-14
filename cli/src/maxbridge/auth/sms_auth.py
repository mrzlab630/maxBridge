"""SMS-based authentication flow for MAX — uses native MaxClient."""

import logging
from typing import Any

from maxbridge.auth.identity import resolve_auth_identity
from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient

logger = logging.getLogger("maxbridge.auth.sms")


async def request_sms_code(client: MaxClient, phone: str) -> str:
    """Request SMS code. Returns sms_token for verify_sms_code()."""
    logger.info("Requesting SMS code for %s...", _mask_phone(phone))
    sms_token = await client.request_sms_code(phone)
    logger.info("SMS code sent successfully")
    return sms_token


async def verify_sms_code(client: MaxClient, sms_token: str, code: int,
                          session: Session) -> dict[str, Any]:
    """Verify SMS code and persist the encrypted session."""
    logger.info("Verifying SMS code...")
    response = await client.verify_sms_code(sms_token, code)

    login_token = client.extract_login_token(response)
    identity = await resolve_auth_identity(
        client,
        response,
        login_token,
        client.device_id,
    )
    session.save(client.device_id, login_token, identity)

    logger.info("SMS authentication successful")
    return response


def _mask_phone(phone: str) -> str:
    """Mask phone number for logging: +7903***5090."""
    if len(phone) < 7:
        return "***"
    return phone[:4] + "***" + phone[-4:]
