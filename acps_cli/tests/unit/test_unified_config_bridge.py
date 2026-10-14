from __future__ import annotations

from pathlib import Path

import pytest
from click import ClickException

from acps_cli.shared.runtime import RootCliRuntime
from acps_cli.shared.unified_config import (
    build_ca_legacy_section,
    build_discovery_runtime_context,
    build_registry_legacy_section,
)


def _runtime(tmp_path: Path, toml_data: dict[str, object]) -> RootCliRuntime:
    config_path = tmp_path / "acps-cli.toml"
    config_path.write_text("# generated for tests\n", encoding="utf-8")
    return RootCliRuntime(
        config_path=str(config_path),
        verbose=False,
        toml_data=toml_data,
        resolved_config_path=config_path,
        config_dir=tmp_path,
    )


def test_registry_bridge_uses_new_base_url_and_auth_token_files(tmp_path: Path) -> None:
    runtime = _runtime(
        tmp_path,
        {
            "registry": {
                "base_url": "http://registry.example.test:9001",
                "mtls_base_url": "https://registry-mtls.example.test:9002",
            },
            "auth": {
                "user_token_file": "tokens/user.json",
                "admin_token_file": "tokens/admin.json",
            },
        },
    )

    user_section = build_registry_legacy_section(runtime, cli_base_url=None, admin=False)
    admin_section = build_registry_legacy_section(runtime, cli_base_url=None, admin=True)

    assert user_section["server_base_url"] == "http://registry.example.test:9001/api/v1"
    assert user_section["mtls_base_url"] == "https://registry-mtls.example.test:9002"
    assert user_section["token_file"].endswith("tokens/user.json")
    assert admin_section["token_file"].endswith("tokens/admin.json")


def test_registry_bridge_accepts_cli_mtls_url_override(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, {"registry": {"base_url": "http://registry.example.test:9001"}})

    section = build_registry_legacy_section(
        runtime,
        cli_base_url=None,
        cli_mtls_url="https://registry-mtls.override.test:9443",
        admin=False,
        require_mtls=True,
    )

    assert section["mtls_base_url"] == "https://registry-mtls.override.test:9443"


def test_registry_bridge_rejects_legacy_registry_key(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, {"registry": {"server_base_url": "http://legacy.example/api/v1"}})

    with pytest.raises(ClickException, match=r"\[registry\]\.server_base_url"):
        build_registry_legacy_section(runtime, cli_base_url=None, admin=False)


def test_ca_bridge_uses_new_base_url(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, {"ca": {"base_url": "http://ca.example.test:9003"}})

    section = build_ca_legacy_section(runtime, cli_base_url=None)

    assert section["server_base_url"] == "http://ca.example.test:9003"


def test_discovery_bridge_uses_new_base_url(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, {"discovery": {"base_url": "http://discovery.example.test:9005"}})

    context = build_discovery_runtime_context(runtime, cli_base_url=None)

    assert context["server_base_url"] == "http://discovery.example.test:9005"
