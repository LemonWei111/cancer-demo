"""Shared helpers for registry 9002 mTLS prerequisite detection in tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_MTLS_START_GUIDANCE = (
    "请使用 registry-server 自身虚拟环境启动 `python -m app.main_mtls`（仅 9002）"
    " 或 `python -m app.runtime_dual_listener`（9001 + 9002），并将 "
    "REGISTRY_MTLS_URL 设置为 https://...，REGISTRY_MTLS_CA_FILE 指向服务端信任锚。"
)


@dataclass(frozen=True)
class RegistryMtlsSettings:
    """Registry 9002 mTLS listener prerequisites detected for tests."""

    base_url: str
    ca_file: Path | None
    skip_reason: str | None = None

    @property
    def is_available(self) -> bool:
        """Return whether entity registration prerequisites are satisfied."""
        return self.skip_reason is None and self.ca_file is not None

    @property
    def unavailable_reason(self) -> str:
        """Return a user-facing reason when mTLS prerequisites are unavailable."""
        return self.skip_reason or "registry-server 9002 mTLS listener prerequisites are unavailable"


def detect_registry_mtls_settings(
    *,
    registry_mtls_url: str,
    registry_mtls_ca_file: str | None,
    wait_for_service: Callable[..., bool],
) -> RegistryMtlsSettings:
    """Detect whether real HTTPS mTLS prerequisites for register-entity are ready."""
    base_url = registry_mtls_url.rstrip("/")

    if not base_url.startswith("https://"):
        return RegistryMtlsSettings(
            base_url=base_url,
            ca_file=None,
            skip_reason=(
                f"register-entity 需要真实 HTTPS mTLS listener，当前 REGISTRY_MTLS_URL={base_url!r}。"
                f" {_MTLS_START_GUIDANCE}"
            ),
        )

    if not registry_mtls_ca_file:
        return RegistryMtlsSettings(
            base_url=base_url,
            ca_file=None,
            skip_reason=(f"register-entity 需要设置 REGISTRY_MTLS_CA_FILE。 {_MTLS_START_GUIDANCE}"),
        )

    ca_file = Path(registry_mtls_ca_file).expanduser()
    if not ca_file.is_file():
        return RegistryMtlsSettings(
            base_url=base_url,
            ca_file=None,
            skip_reason=(f"REGISTRY_MTLS_CA_FILE 不存在或不是文件：{ca_file}。 {_MTLS_START_GUIDANCE}"),
        )

    health_url = f"{base_url}/health"
    if not wait_for_service(
        health_url,
        verify=False,
        accept_remote_protocol_error=True,
    ):
        return RegistryMtlsSettings(
            base_url=base_url,
            ca_file=ca_file,
            skip_reason=(f"registry-server 9002 mTLS listener 不可达：{health_url}。 {_MTLS_START_GUIDANCE}"),
        )

    return RegistryMtlsSettings(base_url=base_url, ca_file=ca_file)
