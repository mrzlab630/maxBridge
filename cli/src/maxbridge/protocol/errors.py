"""Typed exceptions for MAX protocol errors with user-friendly messages."""


class MaxApiError(Exception):
    """Base class for MAX API errors."""

    def __init__(self, error_code: str, message: str, details: str = "") -> None:
        self.error_code = error_code
        self.message = message
        self.details = details
        super().__init__(self.format())

    def format(self) -> str:
        return self.message


_ERROR_MAP: dict[str, str] = {
    "auth.request.forbidden": (
        "Authorization forbidden from this location. "
        "MAX restricts auth by IP geolocation. "
        "Use a proxy/VPN from an allowed country, or authenticate "
        "on a machine in Russia and copy the session files."
    ),
    "auth.code.invalid": "Invalid SMS code. Check and try again.",
    "auth.code.expired": "SMS code expired. Request a new one.",
    "auth.token.expired": "Session token expired. Re-authenticate with SMS.",
    "auth.token.invalid": "Session token invalid. Re-authenticate with SMS.",
    "auth.phone.invalid": "Invalid phone number format. Use +7XXXXXXXXXX.",
    "auth.phone.blocked": "This phone number is blocked.",
    "auth.rate_limit": "Too many auth attempts. Wait and try again.",
}


def raise_for_payload(payload: dict, context: str = "") -> None:
    """Check payload for errors and raise a typed exception if found.

    Call this after any MAX API call to get clear error messages.
    """
    error_code = payload.get("error")
    if not error_code:
        return

    raw_message = payload.get("message", "")
    friendly = _ERROR_MAP.get(error_code, f"MAX API error: {error_code}")

    raise MaxApiError(
        error_code=error_code,
        message=friendly,
        details=f"{context}: {raw_message}" if context else raw_message,
    )


class MaxConnectionError(Exception):
    """WebSocket connection failed."""


class MaxAuthError(MaxApiError):
    """Authentication-specific error."""
