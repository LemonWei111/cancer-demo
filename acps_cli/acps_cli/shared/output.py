"""Unified output formatting shared across CLI modules."""

from __future__ import annotations

import sys

_RED = "\033[0;31m"
_GREEN = "\033[0;32m"
_YELLOW = "\033[1;33m"
_NC = "\033[0m"


def log_info(message: str) -> None:
    """Print green [INFO] log."""
    print(f"{_GREEN}[INFO]{_NC}  {message}", flush=True)


def log_warn(message: str) -> None:
    """Print yellow [WARN] log to stderr."""
    print(f"{_YELLOW}[WARN]{_NC}  {message}", file=sys.stderr, flush=True)


def log_error(message: str) -> None:
    """Print red [ERROR] log to stderr."""
    print(f"{_RED}[ERROR]{_NC} {message}", file=sys.stderr, flush=True)
