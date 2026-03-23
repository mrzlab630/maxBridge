"""Logging setup for maxBridge."""

import logging
import os
import sys


def setup_logging(level: str = "INFO", log_file: str | None = None,
                  fmt: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s") -> logging.Logger:
    """Configure and return the root maxbridge logger."""
    logger = logging.getLogger("maxbridge")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    formatter = logging.Formatter(fmt)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stderr_handler)

    if log_file:
        # Create log file with restricted permissions before handing to FileHandler
        fd = os.open(log_file, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        os.close(fd)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
