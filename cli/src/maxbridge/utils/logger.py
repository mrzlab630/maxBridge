"""Logging setup for maxBridge."""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import TextIO

ERROR_LOG_RELATIVE_PATH = Path("logs/maxbridge-errors.log")
REDACTED = "[REDACTED]"

_SECRET_KEY = (
    r"authorization|x[-_]?api[-_]?key|api[-_ ]?key|auth[-_ ]?secret|"
    r"bot[-_ ]?token|access[-_ ]?token|refresh[-_ ]?token|id[-_ ]?token|"
    r"client[-_ ]?(?:secret|token)|token|secret|password"
)
_BOT_URL_RE = re.compile(
    r"(?i)(https?://api\.telegram\.org/bot)([^/\s\"'?]+)"
)
_TELEGRAM_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])\d{5,16}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_])"
)
_JSON_DOUBLE_RE = re.compile(
    rf'(?i)("(?:{_SECRET_KEY})"\s*:\s*)"(?:\\.|[^"\\])*"'
)
_JSON_SINGLE_RE = re.compile(
    rf"(?i)('(?:{_SECRET_KEY})'\s*:\s*)'(?:\\.|[^'\\])*'"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)\b(authorization)(\s*(?:=|:)\s*)"
    r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\[REDACTED\]|"
    r"[^\r\n,;}&]+)"
)
_ASSIGNMENT_RE = re.compile(
    rf"(?i)\b(?P<key>{_SECRET_KEY})(?P<sep>\s*(?:=|:)\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\[REDACTED\]|"
    r"[^\s,;}\]&]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def redact_secrets(value: object) -> str:
    """Return text with common credentials replaced by a fixed marker."""
    text = str(value)
    text = _BOT_URL_RE.sub(rf"\1{REDACTED}", text)
    text = _TELEGRAM_TOKEN_RE.sub(REDACTED, text)
    text = _JSON_DOUBLE_RE.sub(rf'\1"{REDACTED}"', text)
    text = _JSON_SINGLE_RE.sub(rf"\1'{REDACTED}'", text)
    text = _AUTHORIZATION_RE.sub(rf"\1\2{REDACTED}", text)

    def replace_assignment(match: re.Match[str]) -> str:
        value_text = match.group("value")
        if value_text.startswith(('"', "'")):
            replacement = f"{value_text[0]}{REDACTED}{value_text[0]}"
        else:
            replacement = REDACTED
        return f"{match.group('key')}{match.group('sep')}{replacement}"

    text = _ASSIGNMENT_RE.sub(replace_assignment, text)
    return _BEARER_RE.sub(f"Bearer {REDACTED}", text)


def error_log_path(runtime_root: str | os.PathLike[str]) -> Path:
    """Return the canonical durable error-log path for a runtime root."""
    return Path(runtime_root) / ERROR_LOG_RELATIVE_PATH


def _open_private_append(path: Path) -> TextIO:
    log_dir = path.parent
    try:
        directory_stat = os.lstat(log_dir)
    except FileNotFoundError:
        log_dir.mkdir(parents=True, mode=0o700)
    else:
        if stat.S_ISLNK(directory_stat.st_mode):
            raise OSError(errno.ELOOP, "error log directory is a symlink", log_dir)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise NotADirectoryError(log_dir)

    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode):
            raise OSError(errno.EINVAL, "error log is not a regular file", path)
        os.fchmod(descriptor, 0o600)
        return os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
    except Exception:
        os.close(descriptor)
        raise


class PrivateErrorHandler(logging.Handler):
    """Append redacted error records to the canonical private log."""

    def __init__(self, path: Path, stream: TextIO) -> None:
        super().__init__(level=logging.ERROR)
        self.path = path
        self._stream = stream
        self._exception_formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            traceback_text = ""
            if record.exc_info:
                traceback_text = self._exception_formatter.formatException(
                    record.exc_info
                )
            if record.stack_info:
                traceback_text = "\n".join(
                    part for part in (traceback_text, record.stack_info) if part
                )
            payload = {
                "version": 1,
                "timestamp": datetime.fromtimestamp(record.created)
                .astimezone()
                .isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": redact_secrets(record.name),
                "message": redact_secrets(record.getMessage()),
                "traceback": redact_secrets(traceback_text),
            }
            serialized = json.dumps(payload, ensure_ascii=False)
            descriptor = self._stream.fileno()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                self._stream.write(serialized + "\n")
                self._stream.flush()
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except Exception:
            # Existing stderr/config handlers remain responsible if this sink fails.
            return

    def close(self) -> None:
        try:
            if not self._stream.closed:
                self._stream.flush()
                self._stream.close()
        finally:
            super().close()


def setup_error_logging(
    runtime_root: str | os.PathLike[str],
) -> PrivateErrorHandler:
    """Attach one private redacted error handler for the given runtime root."""
    path = error_log_path(runtime_root).absolute()
    logger = logging.getLogger("maxbridge")
    if logger.getEffectiveLevel() > logging.ERROR:
        logger.setLevel(logging.ERROR)
    for handler in logger.handlers:
        if isinstance(handler, PrivateErrorHandler) and handler.path == path:
            return handler

    handler = PrivateErrorHandler(path, _open_private_append(path))
    logger.addHandler(handler)
    return handler


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    fmt: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
) -> logging.Logger:
    """Configure and return the root maxbridge logger."""
    logger = logging.getLogger("maxbridge")
    configured_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(min(configured_level, logging.ERROR))

    formatter = logging.Formatter(fmt)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(configured_level)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_file:
        # Create log file with restricted permissions before handing to FileHandler
        fd = os.open(log_file, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        os.close(fd)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(configured_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
