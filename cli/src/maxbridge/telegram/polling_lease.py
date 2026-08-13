"""Local single-owner lease for Telegram long polling."""

import errno
import hashlib
import socket


class TelegramPollingLeaseError(RuntimeError):
    """Base class for sanitized local polling ownership errors."""


class TelegramPollingLeaseConflict(TelegramPollingLeaseError):
    """Another cooperating local process owns this bot's polling lease."""


class TelegramPollingLeaseUnavailable(TelegramPollingLeaseError):
    """The runtime cannot provide the required fail-closed local lease."""


class TelegramPollingLease:
    """Retain a Linux abstract AF_UNIX socket keyed only by bot identity."""

    def __init__(self, bot_token: str) -> None:
        digest = hashlib.sha256(bot_token.encode("utf-8")).hexdigest()[:32]
        if len(digest) != 32 or any(char not in "0123456789abcdef" for char in digest):
            raise TelegramPollingLeaseUnavailable(
                "Telegram polling lease identity is unavailable",
            )
        self._address = "\0maxbridge.telegram.poller." + digest
        self._socket: socket.socket | None = None

    @property
    def held(self) -> bool:
        return self._socket is not None

    def acquire(self) -> None:
        if self._socket is not None:
            return
        try:
            lease_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        except (AttributeError, OSError):
            raise TelegramPollingLeaseUnavailable(
                "Telegram polling lease is unavailable on this runtime",
            ) from None
        try:
            lease_socket.bind(self._address)
        except OSError as exc:
            lease_socket.close()
            if exc.errno == errno.EADDRINUSE:
                raise TelegramPollingLeaseConflict(
                    "Telegram polling is already active in this runtime scope",
                ) from None
            raise TelegramPollingLeaseUnavailable(
                "Telegram polling lease is unavailable on this runtime",
            ) from None
        self._socket = lease_socket

    def release(self) -> None:
        lease_socket = self._socket
        if lease_socket is None:
            return
        self._socket = None
        lease_socket.close()
