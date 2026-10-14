import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CliOverrides:
    """命令行覆盖项，优先级最高。"""

    server_base_url: str | None = None


class Config:
    """从 TOML [ca] section 与环境变量加载 CA 客户端配置。

    优先级：CLI 选项 > 环境变量 > TOML 配置文件 > 默认值。
    """

    def __init__(
        self,
        toml_section: dict[str, Any],
        overrides: CliOverrides | None = None,
        config_file_path: str | None = None,
    ) -> None:
        self._toml = toml_section
        self._overrides = overrides or CliOverrides()
        self._config_file_path = Path(config_file_path).expanduser().resolve() if config_file_path else None
        self._config_dir = self._config_file_path.parent if self._config_file_path else None

    def _resolve(
        self,
        env_key: str,
        toml_key: str,
        default: str,
        cli_value: str | None = None,
    ) -> str:
        """按优先级解析配置项：CLI > 环境变量 > TOML > 默认值。"""
        if cli_value not in (None, ""):
            return str(cli_value)
        env_val = os.environ.get(env_key)
        if env_val not in (None, ""):
            return str(env_val)
        toml_val = self._toml.get(toml_key)
        if toml_val is not None:
            return str(toml_val)
        return default

    def _get_required_url(
        self,
        env_key: str,
        toml_key: str,
        cli_value: str | None = None,
    ) -> str:
        val = self._resolve(env_key, toml_key, "", cli_value=cli_value)
        if not val:
            sys.stderr.write(f"Error: Missing required configuration '{toml_key}'.\n")
            sys.exit(2)
        parsed = urlparse(val)
        if not all([parsed.scheme, parsed.netloc]):
            sys.stderr.write(f"Error: Configuration '{toml_key}' is not a valid URL: '{val}'.\n")
            sys.exit(2)
        return val

    def _resolve_path(self, env_key: str, toml_key: str, default: str) -> str:
        env_val = os.environ.get(env_key)
        if env_val not in (None, ""):
            return self._resolve_config_relative_path(str(env_val))

        toml_val = self._toml.get(toml_key)
        if toml_val not in (None, ""):
            return self._resolve_config_relative_path(str(toml_val))

        return self._resolve_config_relative_path(default)

    def _resolve_config_relative_path(self, path_value: str) -> str:
        path = Path(path_value).expanduser()
        if path.is_absolute() or self._config_dir is None:
            return str(path)
        return str((self._config_dir / path).resolve())

    @property
    def ca_server_base_url(self) -> str:
        raw_url = self._get_required_url(
            "CA_SERVER_BASE_URL",
            "server_base_url",
            cli_value=self._overrides.server_base_url,
        )
        normalized_url = raw_url.rstrip("/")
        if normalized_url.endswith("/acps-atr-v2"):
            return normalized_url[: -len("/acps-atr-v2")]
        return normalized_url

    @property
    def ca_server_atr_base_url(self) -> str:
        return f"{self.ca_server_base_url}/acps-atr-v2"

    @property
    def ca_server_url(self) -> str:
        """兼容旧调用方，返回派生后的 ATR 根地址。"""
        return self.ca_server_atr_base_url

    @property
    def admin_api_token(self) -> str | None:
        token = self._resolve(
            "CA_SERVER_ADMIN_API_TOKEN",
            "admin_api_token",
            "",
        ).strip()
        return token or None

    @property
    def account_keys_dir(self) -> str:
        return self._resolve_path("CA_ACCOUNT_KEYS_DIR", "account_keys_dir", "./keyfiles/accounts")

    def account_key_path_for(self, aic: str) -> str:
        """返回指定 AIC 的 account key 文件路径。"""
        return os.path.join(self.account_keys_dir, f"{aic}.account.key")

    def legacy_account_key_paths(self) -> list[str]:
        """返回旧版单 account key 布局的兼容候选路径。"""
        return [os.path.join(self.account_keys_dir, "account.key")]

    @property
    def certs_dir(self) -> str:
        return self._resolve_path("CA_CERTS_DIR", "certs_dir", "./keyfiles/certs")

    @property
    def private_keys_dir(self) -> str:
        return self._resolve_path("CA_PRIVATE_KEYS_DIR", "private_keys_dir", "./keyfiles/private")

    @property
    def csr_dir(self) -> str:
        return self._resolve_path("CA_CSR_DIR", "csr_dir", "./keyfiles/csr")

    @property
    def trust_bundle_path(self) -> str:
        return self._resolve_path(
            "CA_TRUST_BUNDLE_PATH",
            "trust_bundle_path",
            "./keyfiles/trust-bundle.pem",
        )
