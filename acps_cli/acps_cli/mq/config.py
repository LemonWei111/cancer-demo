"""MqConfig：从 TOML [mq] 节加载 mq-auth-server 客户端配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _resolve_path(base_dir: Path | None, value: str) -> str:
    """将相对路径相对于 base_dir 解析为绝对路径；绝对路径原样返回。
    无 base_dir 时相对路径保持原样（不做进一步展开）。
    """
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    if base_dir is not None:
        return str((base_dir / path).resolve())
    return str(path)


def _resolve_optional_path(base_dir: Path | None, value: str | None) -> str | None:
    """可选路径解析；None 原样返回。"""
    if value is None:
        return None
    return _resolve_path(base_dir, value)


@dataclass(frozen=True)
class MqConfig:
    """mq-auth-server 客户端配置。

    证书分为两类：
    - group_cert_file / group_key_file：Leader 专属，CN 须等于 leader_aic；
      group 子命令使用，若留空则要求命令行必须传 --cert-file / --key-file。
    - probe_cert_file / probe_key_file：任意合法 ACPs 证书；
      health / auth-probe 命令使用，若留空则回退到 [ca] 目录推导路径。
    """

    group_api_url: str
    auth_api_url: str
    group_cert_file: str | None  # group 命令 Leader 证书
    group_key_file: str | None
    probe_cert_file: str | None  # health / auth-probe 证书
    probe_key_file: str | None
    ca_cert_file: str | None
    timeout_seconds: int

    @classmethod
    def from_toml(
        cls,
        data: dict[str, Any],
        config_dir: Path | None,
        *,
        cli_group_api_url: str | None = None,
        cli_auth_api_url: str | None = None,
    ) -> MqConfig:
        """从 toml_data["mq"] 加载；CLI/环境变量优先于 TOML，相对路径相对于 config_dir 解析。

        环境变量：
            MQ_GROUP_API_URL, MQ_AUTH_API_URL,
            MQ_GROUP_CERT_FILE, MQ_GROUP_KEY_FILE,
            MQ_PROBE_CERT_FILE, MQ_PROBE_KEY_FILE,
            MQ_CA_FILE
        """

        def _str(cli_value: str | None, env: str, key: str, default: str) -> str:
            if cli_value is not None and cli_value != "":
                return cli_value
            env_value = os.environ.get(env)
            if env_value is not None and env_value != "":
                return env_value
            toml_value = data.get(key)
            if toml_value not in (None, ""):
                return str(toml_value)
            return default

        def _optional(env: str, key: str) -> str | None:
            env_val = os.environ.get(env)
            if env_val:
                return _resolve_path(config_dir, env_val)
            toml_val = data.get(key)
            if toml_val:
                return _resolve_path(config_dir, str(toml_val))
            return None

        group_api_url = _str(
            cli_group_api_url,
            "MQ_GROUP_API_URL",
            "group_api_url",
            "https://localhost:9007",
        )
        auth_api_url = _str(
            cli_auth_api_url,
            "MQ_AUTH_API_URL",
            "auth_api_url",
            "https://localhost:9008",
        )

        timeout_seconds_raw = os.environ.get("MQ_TIMEOUT_SECONDS") or data.get("timeout_seconds")
        try:
            timeout_seconds = int(timeout_seconds_raw) if timeout_seconds_raw is not None else 10
        except (ValueError, TypeError):
            timeout_seconds = 10

        return cls(
            group_api_url=group_api_url.rstrip("/"),
            auth_api_url=auth_api_url.rstrip("/"),
            group_cert_file=_optional("MQ_GROUP_CERT_FILE", "group_cert_file"),
            group_key_file=_optional("MQ_GROUP_KEY_FILE", "group_key_file"),
            probe_cert_file=_optional("MQ_PROBE_CERT_FILE", "probe_cert_file"),
            probe_key_file=_optional("MQ_PROBE_KEY_FILE", "probe_key_file"),
            ca_cert_file=_optional("MQ_CA_FILE", "ca_cert_file"),
            timeout_seconds=timeout_seconds,
        )
