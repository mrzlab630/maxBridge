"""Token-based re-authentication for MAX (using saved session)."""

import logging
from typing import Any

from maxbridge.auth.session import Session
from maxbridge.protocol.max_client import MaxClient

logger = logging.getLogger("maxbridge.auth.token")


async def login_with_token(client: MaxClient, session: Session) -> dict[str, Any]:
    """Authenticate using a previously saved session token.

    Only clears session on explicit auth rejection, NOT on transient errors.
    Raises RuntimeError if the session is invalid or expired.
    """
    if not session.device_id or not session.token:
        raise RuntimeError("No saved session — SMS authentication required")

    logger.info("Logging in with saved token...")
    try:
        result = await client.login_by_token(session.token, session.device_id)
        logger.info("Token authentication successful")
        return result
    except Exception as e:
        error_str = str(e).lower()
        # Only clear session on explicit auth rejection, not transient errors
        if any(kw in error_str for kw in ("expired", "invalid", "unauthorized", "forbidden")):
            logger.warning("Token rejected — clearing session")
            session.clear()
        else:
            logger.exception("Token login failed (transient error — session preserved)")
        raise RuntimeError("Token login failed — re-authenticate with SMS") from e
