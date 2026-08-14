"""Token-based re-authentication for MAX (using saved session)."""

import logging
from typing import Any

from maxbridge.auth.identity import extract_max_contact_id
from maxbridge.auth.session import Session
from maxbridge.protocol.errors import MaxApiError, MaxAuthRequiredError
from maxbridge.protocol.max_client import MaxClient

logger = logging.getLogger("maxbridge.auth.token")


async def login_with_token(
    client: MaxClient,
    session: Session,
    *,
    clear_session_on_rejection: bool = True,
) -> dict[str, Any]:
    """Authenticate using a previously saved session token.

    Only clears session on explicit auth rejection, NOT on transient errors.
    Raises MaxAuthRequiredError if the session is invalid or expired.
    """
    if not session.device_id or not session.token:
        raise MaxAuthRequiredError("No saved session — QR authentication required")

    logger.info("Logging in with saved token...")
    try:
        result = await client.login_by_token(session.token, session.device_id)
        identity = extract_max_contact_id(result)
        if identity is not None and identity != session.max_contact_id:
            session.save(session.device_id, session.token, identity)
        logger.info("Token authentication successful")
        return result
    except Exception as e:
        if _is_auth_rejection(e):
            if clear_session_on_rejection:
                logger.warning("Token rejected — clearing session")
                session.clear()
            else:
                logger.warning("Token rejected during identity verification")
            raise MaxAuthRequiredError(
                "Token login failed — re-authenticate with QR",
            ) from e

        logger.warning(
            "Token login failed (transient error — session preserved): %s: %s",
            e.__class__.__name__,
            e,
        )
        raise RuntimeError("Token login failed — transient error, session preserved") from e


def _is_auth_rejection(exc: Exception) -> bool:
    """Detect explicit auth rejection from MAX or gateway layers."""
    if isinstance(exc, MaxApiError):
        code = exc.error_code.lower()
        if any(marker in code for marker in ("token", "auth", "login.token", "unauthorized")):
            return True

    error_str = str(exc).lower()
    return any(
        kw in error_str
        for kw in (
            "expired",
            "invalid",
            "unauthorized",
            "forbidden",
            "login.token",
            "auth.token",
        )
    )
