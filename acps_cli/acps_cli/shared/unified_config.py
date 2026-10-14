"""Config bridge helpers for the unified CLI command tree."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import click

from acps_cli.shared.runtime import RootCliRuntime


def _section(runtime: RootCliRuntime, name: str) -> dict[str, Any]:
    value = runtime.toml_data.get(name, {})
    return value if isinstance(value, dict) else {}


def _normalize_url(label: str, value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise click.ClickException(f"Invalid {label}: {value}")
    return value.rstrip("/")


def _resolve_path(base_dir: Path, value: str) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _reject_legacy_toml_keys(section_name: str, section: dict[str, Any], replacements: dict[str, str]) -> None:
    for old_key, new_key in replacements.items():
        if old_key in section:
            raise click.ClickException(
                f"Config key [{section_name}].{old_key} is no longer supported. Use {new_key} instead."
            )


def _reject_legacy_env_keys(replacements: dict[str, str]) -> None:
    for old_key, new_key in replacements.items():
        if os.getenv(old_key) not in (None, ""):
            raise click.ClickException(f"Environment variable {old_key} is no longer supported. Use {new_key} instead.")


def build_registry_legacy_section(
    runtime: RootCliRuntime,
    *,
    cli_base_url: str | None,
    cli_mtls_url: str | None = None,
    admin: bool,
    require_mtls: bool = False,
) -> dict[str, Any]:
    registry_section = dict(_section(runtime, "registry"))
    auth_section = _section(runtime, "auth")
    base_dir = runtime.config_dir or Path.cwd()

    _reject_legacy_toml_keys(
        "registry",
        registry_section,
        {
            "server_base_url": "[registry].base_url",
            "atr_base_url": "internal derived ATR path from [registry].base_url",
            "token_file": "[auth].user_token_file / [auth].admin_token_file",
        },
    )
    _reject_legacy_env_keys(
        {
            "REGISTRY_SERVER_BASE_URL": "REGISTRY_BASE_URL",
            "REGISTRY_API_BASE_URL": "REGISTRY_BASE_URL",
            "REGISTRY_ATR_BASE_URL": "derived ATR path from REGISTRY_BASE_URL",
            "REGISTRY_TOKEN_FILE": "AUTH_USER_TOKEN_FILE or AUTH_ADMIN_TOKEN_FILE",
        }
    )

    base_url = _normalize_url(
        "REGISTRY_BASE_URL",
        str(
            cli_base_url
            or os.getenv("REGISTRY_BASE_URL")
            or registry_section.get("base_url")
            or "http://localhost:9001"
        ),
    )
    mtls_value = cli_mtls_url or os.getenv("REGISTRY_MTLS_BASE_URL") or registry_section.get("mtls_base_url")
    if require_mtls and not mtls_value:
        raise click.ClickException(
            "registry.mtls_base_url is required for entity derive. "
            "Configure [registry].mtls_base_url, REGISTRY_MTLS_BASE_URL, or pass --mtls-url."
        )

    token_key = "admin_token_file" if admin else "user_token_file"
    token_env_key = "AUTH_ADMIN_TOKEN_FILE" if admin else "AUTH_USER_TOKEN_FILE"
    token_default_name = "registry-admin.json" if admin else "registry-user.json"
    token_value = str(
        os.getenv(token_env_key)
        or auth_section.get(token_key)
        or (base_dir / ".acps-cli" / "tokens" / token_default_name)
    )

    legacy_section = dict(registry_section)
    legacy_section["server_base_url"] = f"{base_url}/api/v1"
    legacy_section["token_file"] = _resolve_path(base_dir, token_value)
    if mtls_value:
        legacy_section["mtls_base_url"] = _normalize_url("REGISTRY_MTLS_BASE_URL", str(mtls_value))
    return legacy_section


def build_ca_legacy_section(runtime: RootCliRuntime, *, cli_base_url: str | None) -> dict[str, Any]:
    ca_section = dict(_section(runtime, "ca"))

    _reject_legacy_toml_keys(
        "ca",
        ca_section,
        {
            "server_base_url": "[ca].base_url",
        },
    )
    _reject_legacy_env_keys(
        {
            "CA_SERVER_BASE_URL": "CA_BASE_URL",
            "CA_SERVER_ATR_BASE_URL": "derived ATR path from CA_BASE_URL",
        }
    )

    base_url = _normalize_url(
        "CA_BASE_URL",
        str(cli_base_url or os.getenv("CA_BASE_URL") or ca_section.get("base_url") or "http://localhost:9003"),
    )
    legacy_section = dict(ca_section)
    legacy_section["server_base_url"] = base_url
    return legacy_section


def build_discovery_runtime_context(runtime: RootCliRuntime, *, cli_base_url: str | None) -> dict[str, Any]:
    discovery_section = dict(_section(runtime, "discovery"))

    _reject_legacy_toml_keys(
        "discovery",
        discovery_section,
        {
            "server_base_url": "[discovery].base_url",
        },
    )
    _reject_legacy_env_keys(
        {
            "DISCOVERY_SERVER_BASE_URL": "DISCOVERY_BASE_URL",
        }
    )

    base_url = _normalize_url(
        "DISCOVERY_BASE_URL",
        str(
            cli_base_url
            or os.getenv("DISCOVERY_BASE_URL")
            or discovery_section.get("base_url")
            or "http://localhost:9005"
        ),
    )
    return {
        "server_base_url": base_url,
        "toml_data": runtime.toml_data,
        "config_dir": runtime.config_dir,
    }
