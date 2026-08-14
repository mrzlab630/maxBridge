"""Verified MAX account identity helpers."""

from typing import Any

from maxbridge.protocol.max_client import MaxClient


class MaxIdentityUnavailableError(RuntimeError):
    """Raised when MAX does not provide a usable factual profile identity."""


class DuplicateMaxIdentityError(RuntimeError):
    """Raised when a new authorization duplicates an existing MAX account."""

    def __init__(self, account_id: str, duplicate_of: str) -> None:
        self.account_id = account_id
        self.duplicate_of = duplicate_of
        super().__init__(
            f"Аккаунт '{account_id}' уже авторизован как '{duplicate_of}'. "
            "Новая сессия не сохранена."
        )


def normalize_max_contact_id(value: object) -> str | None:
    """Return the canonical numeric MAX profile contact ID, if valid."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit() and int(normalized) > 0:
            return str(int(normalized))
    return None


def extract_max_contact_id(response: object) -> str | None:
    """Extract payload.profile.contact.id without deriving identity elsewhere."""
    if not isinstance(response, dict):
        return None
    payload = response.get("payload")
    if not isinstance(payload, dict):
        return None
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return None
    contact = profile.get("contact")
    if not isinstance(contact, dict):
        return None
    return normalize_max_contact_id(contact.get("id"))


async def resolve_auth_identity(
    client: MaxClient,
    auth_response: dict[str, Any],
    token: str,
    device_id: str,
) -> str:
    """Verify a new authorization identity, falling back to token login."""
    identity = extract_max_contact_id(auth_response)
    if identity is None:
        login_response = await client.login_by_token(token, device_id)
        identity = extract_max_contact_id(login_response)
    if identity is None:
        raise MaxIdentityUnavailableError(
            "MAX не вернул проверенный идентификатор профиля. "
            "Авторизация не сохранена."
        )
    return identity
