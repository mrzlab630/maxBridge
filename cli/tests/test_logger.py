"""Tests for the private redacted error sink."""

import json
import logging
import os
import stat
from unittest.mock import MagicMock

import pytest

from maxbridge.utils.logger import (
    REDACTED,
    PrivateErrorHandler,
    redact_secrets,
    setup_error_logging,
    setup_logging,
)


@pytest.fixture
def isolated_maxbridge_logger():
    logger = logging.getLogger("maxbridge")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger
    finally:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        (
            "telegram token 123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
            "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        ),
        (
            "https://api.telegram.org/bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi/sendMessage",
            "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        ),
        ("Authorization: Bearer bearer-secret-value", "bearer-secret-value"),
        ("Authorization: Basic basic-secret-value", "basic-secret-value"),
        ("Bearer standalone-secret-value", "standalone-secret-value"),
        ("auth_secret=auth-secret-value", "auth-secret-value"),
        ("bot_token: bot-token-value", "bot-token-value"),
        ("api_key='api-key-value'", "api-key-value"),
        ("API-Key: api-header-value", "api-header-value"),
        ("x-api-key: x-api-header-value", "x-api-header-value"),
        ("password = \"password-value\"", "password-value"),
        ("token=token-value&next=1", "token-value"),
        ("?access_token=access-query-value&next=1", "access-query-value"),
        ("refresh_token=refresh-value", "refresh-value"),
        ("id_token: id-token-value", "id-token-value"),
        ("client_secret=client-secret-value", "client-secret-value"),
        ("client_token=client-token-value", "client-token-value"),
        ("accessToken=camel-access-value", "camel-access-value"),
        ("refreshToken: camel-refresh-value", "camel-refresh-value"),
        ("idToken='camel-id-value'", "camel-id-value"),
        ("clientSecret=camel-client-secret", "camel-client-secret"),
        ("botToken=camel-bot-token", "camel-bot-token"),
        ("apiKey=camel-api-key", "camel-api-key"),
        ('{"secret": "json-secret-value"}', "json-secret-value"),
        ('{"refresh_token": "json-refresh-value"}', "json-refresh-value"),
        ('{"clientSecret": "json-client-secret"}', "json-client-secret"),
        ("{'bot_token': 'single-json-value'}", "single-json-value"),
    ],
)
def test_redact_secrets_removes_common_credential_forms(raw, secret):
    redacted = redact_secrets(raw)

    assert secret not in redacted
    assert REDACTED in redacted
    assert redact_secrets(redacted) == redacted


def test_redact_secrets_does_not_hide_non_assignment_words():
    assert redact_secrets("token expired; password required") == (
        "token expired; password required"
    )


def test_private_error_handler_is_append_only_private_and_idempotent(
    tmp_path, isolated_maxbridge_logger
):
    log_path = tmp_path / "logs/maxbridge-errors.log"

    first = setup_error_logging(tmp_path)
    second = setup_error_logging(tmp_path)
    isolated_maxbridge_logger.warning("warning must not be durable")
    isolated_maxbridge_logger.error("request failed token=top-secret-token")

    assert first is second
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["level"] == "ERROR"
    assert record["message"] == f"request failed token={REDACTED}"
    assert "top-secret-token" not in lines[0]
    assert stat.S_IMODE(log_path.stat().st_mode) == 0o600


def test_private_error_handler_preserves_redacted_traceback(
    tmp_path, isolated_maxbridge_logger
):
    log_path = tmp_path / "logs/maxbridge-errors.log"
    setup_error_logging(tmp_path)
    secret = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"

    try:
        raise RuntimeError(
            f"Authorization: Bearer traceback-bearer {secret}"
        )
    except RuntimeError:
        isolated_maxbridge_logger.exception(
            "Bot URL https://api.telegram.org/bot%s/sendMessage", secret
        )

    stored = log_path.read_text(encoding="utf-8")
    record = json.loads(stored)
    assert "Traceback (most recent call last)" in record["traceback"]
    assert "RuntimeError" in record["traceback"]
    assert REDACTED in stored
    assert secret not in stored
    assert "traceback-bearer" not in stored


def test_critical_config_threshold_still_captures_errors_only_in_private_sink(
    tmp_path, isolated_maxbridge_logger, monkeypatch
):
    stderr = MagicMock()
    monkeypatch.setattr("maxbridge.utils.logger.sys.stderr", stderr)
    setup_logging(level="CRITICAL")
    setup_error_logging(tmp_path)

    isolated_maxbridge_logger.error("durable error")

    stored = (tmp_path / "logs/maxbridge-errors.log").read_text(encoding="utf-8")
    assert "durable error" in stored
    assert not stderr.write.called


@pytest.mark.skipif(
    not hasattr(os, "O_NOFOLLOW"), reason="platform does not support O_NOFOLLOW"
)
def test_private_error_handler_rejects_symlink_without_mutating_target(
    tmp_path, isolated_maxbridge_logger
):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    target = tmp_path / "external.log"
    target.write_text("external\n", encoding="utf-8")
    os.chmod(target, 0o640)
    (log_dir / "maxbridge-errors.log").symlink_to(target)

    with pytest.raises(OSError):
        setup_error_logging(tmp_path)

    assert target.read_text(encoding="utf-8") == "external\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert not any(
        isinstance(handler, PrivateErrorHandler)
        for handler in isolated_maxbridge_logger.handlers
    )
