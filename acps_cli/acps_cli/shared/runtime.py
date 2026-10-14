"""Shared runtime helpers for the unified CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from acps_cli.shared.cli_logging import setup_cli_logging
from acps_cli.shared.config import load_toml_config


@dataclass(frozen=True)
class RootCliRuntime:
    """Root-level runtime settings shared by all unified CLI command domains."""

    config_path: str | None
    verbose: bool
    toml_data: dict[str, Any]
    resolved_config_path: Path | None
    config_dir: Path | None


def initialize_root_runtime(ctx: click.Context, config_path: str | None, verbose: bool) -> RootCliRuntime:
    """Load root runtime settings and persist them on the root Click context."""
    setup_cli_logging(verbose)
    toml_data, resolved_path = load_toml_config(config_path)
    runtime = RootCliRuntime(
        config_path=config_path,
        verbose=verbose,
        toml_data=toml_data,
        resolved_config_path=resolved_path,
        config_dir=resolved_path.parent if resolved_path else None,
    )
    ctx.ensure_object(dict)
    ctx.obj["root_runtime"] = runtime
    return runtime


def get_root_runtime(ctx: click.Context) -> RootCliRuntime:
    """Fetch the shared runtime from the root Click context."""
    root_obj = ctx.find_root().obj or {}
    runtime = root_obj.get("root_runtime")
    if not isinstance(runtime, RootCliRuntime):
        raise click.ClickException("Unified CLI runtime is not initialized.")
    return runtime
