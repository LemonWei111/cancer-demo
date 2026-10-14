"""Shared CLI logging setup for all subcommands."""

from __future__ import annotations

import logging
import sys


class CliFormatter(logging.Formatter):
    """Compact formatter for human-facing CLI logs."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno == logging.DEBUG:
            return f"[DEBUG] {message}"
        if record.levelno == logging.WARNING:
            return f"[WARNING] {message}"
        if record.levelno >= logging.ERROR:
            return f"[ERROR] {message}"
        return message


def setup_cli_logging(verbose: bool) -> None:
    """Configure root logger for CLI usage.

    All logs go to stderr so structured command output can keep stdout clean.
    """

    level = logging.DEBUG if verbose else logging.INFO
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    formatter = CliFormatter()

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(level)
    stderr_handler.setFormatter(formatter)

    root_logger.addHandler(stderr_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
