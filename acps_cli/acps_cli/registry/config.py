"""Registry 客户端配置加载模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import ParseResult, urlparse, urlunparse


@dataclass(frozen=True)
class CliOverrides:
    """命令行覆盖项，优先级最高。"""

    server_base_url: str | None = None
    atr_base_url: str | None = None
    mtls_base_url: str | None = None
    timeout_seconds: int | None = None
    token_file: str | None = None
    username: str | None = None
    password: str | None = None


class ConfigError(RuntimeError):
    """配置无效时抛出的异常。"""


def _infer_default_atr_base_url(api_base_url: str) -> str:
    base = api_base_url.rstrip("/")
    if base.endswith("/api/v1"):
        base = base[:-7]
    elif base.endswith("/api"):
        base = base[:-4]
    return f"{base}/acps-atr-v2"


def _build_netloc(parsed: ParseResult, port: int) -> str:
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    credentials = ""
    if parsed.username and parsed.password:
        credentials = f"{parsed.username}:{parsed.password}@"
    elif parsed.username:
        credentials = f"{parsed.username}@"
    return f"{credentials}{host}:{port}"


def _infer_default_mtls_base_url(api_base_url: str) -> str:
    atr_root = _infer_default_atr_base_url(api_base_url)
    parsed = urlparse(atr_root)
    netloc = parsed.netloc
    if parsed.port == 9001:
        netloc = _build_netloc(parsed, 9002)
    return urlunparse(parsed._replace(netloc=netloc, path=parsed.path[:-12], params="", query="", fragment="")).rstrip(
        "/"
    )


class Config:
    """从 TOML [registry] section 与环境变量加载配置值。

    优先级：CLI 选项 > 环境变量 > TOML 配置文件 > 默认值。
    凭证（用户名/密码）仅从环境变量或 .env 文件读取，不写入 TOML。
    """

    CONFIG_KEY_ALIASES: ClassVar[dict[str, tuple[str, ...]]] = {
        "REGISTRY_SERVER_BASE_URL": ("REGISTRY_API_BASE_URL",),
    }
    CREDENTIAL_KEY_ALIASES: ClassVar[dict[str, dict[str, tuple[str, ...]]]] = {
        "REGISTRY_USER": {
            "USERNAME": ("REGISTRY_CLIENT_USERNAME",),
            "PASSWORD": ("REGISTRY_CLIENT_PASSWORD",),
            "NAME": ("REGISTRY_CLIENT_NAME",),
            "ORG_NAME": ("REGISTRY_CLIENT_ORG",),
        },
        "REGISTRY_ADMIN": {
            "USERNAME": (),
            "PASSWORD": (),
            "NAME": (),
            "ORG_NAME": (),
        },
    }

    def __init__(
        self,
        toml_section: dict[str, Any],
        overrides: CliOverrides | None = None,
        credential_env_prefix: str = "REGISTRY_USER",
        default_token_name: str = "registry-user.json",  # noqa: S107
        config_file_dir: Path | None = None,
    ) -> None:
        self._toml = toml_section
        self._overrides = overrides or CliOverrides()
        self._credential_env_prefix = credential_env_prefix
        self._default_token_name = default_token_name
        self._config_file_dir = config_file_dir or Path.cwd()

    def _get_raw(
        self,
        env_key: str,
        toml_key: str,
        default: str | None = None,
        cli_value: str | None = None,
    ) -> str | None:
        """按优先级解析非凭证配置项：CLI > 环境变量（含别名）> TOML > 默认值。"""
        if cli_value is not None:
            return cli_value
        for candidate_key in (env_key, *self.CONFIG_KEY_ALIASES.get(env_key, ())):
            env_value = os.getenv(candidate_key)
            if env_value not in (None, ""):
                return env_value
        toml_val = self._toml.get(toml_key)
        if toml_val is not None:
            return str(toml_val)
        return default

    def _get_credential(self, suffix: str, cli_value: str | None = None) -> str | None:
        """解析敏感凭证字段：CLI > 环境变量（含别名）。用户名/密码不从 TOML 读取。"""
        if cli_value is not None:
            return cli_value
        key = f"{self._credential_env_prefix}_{suffix}"
        aliases = self.CREDENTIAL_KEY_ALIASES.get(self._credential_env_prefix, {}).get(suffix, ())
        for candidate_key in (key, *aliases):
            val = os.getenv(candidate_key)
            if val not in (None, ""):
                return val
        return None

    def _get_profile_field(self, suffix: str, toml_key: str) -> str | None:
        """解析非敏感注册资料：环境变量（兼容旧别名）> TOML。"""
        key = f"{self._credential_env_prefix}_{suffix}"
        aliases = self.CREDENTIAL_KEY_ALIASES.get(self._credential_env_prefix, {}).get(suffix, ())
        for candidate_key in (key, *aliases):
            val = os.getenv(candidate_key)
            if val not in (None, ""):
                return val
        toml_val = self._toml.get(toml_key)
        if toml_val is not None:
            return str(toml_val)
        return None

    def _resolve_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
        return self.project_dir / path

    @property
    def project_dir(self) -> Path:
        return self._config_file_dir

    @property
    def server_base_url(self) -> str:
        value = self._get_raw(
            "REGISTRY_SERVER_BASE_URL",
            "server_base_url",
            default="http://localhost:9001/api/v1",
            cli_value=self._overrides.server_base_url,
        )
        assert value is not None
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(f"Invalid REGISTRY_SERVER_BASE_URL: {value}")
        return value.rstrip("/")

    @property
    def atr_base_url(self) -> str:
        value = self._get_raw(
            "REGISTRY_ATR_BASE_URL",
            "atr_base_url",
            default=_infer_default_atr_base_url(self.server_base_url),
            cli_value=self._overrides.atr_base_url,
        )
        assert value is not None
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(f"Invalid REGISTRY_ATR_BASE_URL: {value}")
        return value.rstrip("/")

    @property
    def mtls_base_url(self) -> str:
        value = self._get_raw(
            "REGISTRY_MTLS_BASE_URL",
            "mtls_base_url",
            default=_infer_default_mtls_base_url(self.server_base_url),
            cli_value=self._overrides.mtls_base_url,
        )
        assert value is not None
        parsed = urlparse(value)
        if not parsed.scheme or not parsed.netloc:
            raise ConfigError(f"Invalid REGISTRY_MTLS_BASE_URL: {value}")
        return value.rstrip("/")

    @property
    def timeout_seconds(self) -> int:
        cli_value = None if self._overrides.timeout_seconds is None else str(self._overrides.timeout_seconds)
        value = self._get_raw(
            "REGISTRY_TIMEOUT_SECONDS",
            "timeout_seconds",
            default="15",
            cli_value=cli_value,
        )
        assert value is not None
        timeout = int(value)
        if timeout <= 0:
            raise ConfigError("REGISTRY_TIMEOUT_SECONDS must be a positive integer")
        return timeout

    @property
    def token_file(self) -> Path:
        default_path = self.project_dir / ".registry-client" / self._default_token_name
        token_path = self._get_raw(
            "REGISTRY_TOKEN_FILE",
            "token_file",
            default=str(default_path),
            cli_value=self._overrides.token_file,
        )
        assert token_path is not None
        return self._resolve_path(token_path)

    @property
    def ontology_mtls_materials_dir(self) -> Path:
        value = self._get_raw(
            "REGISTRY_ONTOLOGY_MTLS_MATERIALS_DIR",
            "ontology_mtls_materials_dir",
            default=str(self.project_dir / ".registry-client" / "ontology-mtls"),
        )
        assert value is not None
        return self._resolve_path(value)

    @property
    def mtls_server_ca_file(self) -> Path | None:
        value = self._get_raw(
            "REGISTRY_MTLS_SERVER_CA_FILE",
            "mtls_server_ca_file",
            default=None,
        )
        if value is None or value == "":
            return None
        return self._resolve_path(value)

    def resolve_ontology_mtls_cert_file(self, ontology_aic: str) -> Path:
        normalized_aic = ontology_aic.strip().upper()
        return self.ontology_mtls_materials_dir / normalized_aic / "certificate.pem"

    def resolve_ontology_mtls_key_file(self, ontology_aic: str) -> Path:
        normalized_aic = ontology_aic.strip().upper()
        return self.ontology_mtls_materials_dir / normalized_aic / "private-key.pem"

    @property
    def username(self) -> str | None:
        return self._get_credential("USERNAME", cli_value=self._overrides.username)

    @property
    def password(self) -> str | None:
        return self._get_credential("PASSWORD", cli_value=self._overrides.password)

    @property
    def display_name(self) -> str | None:
        return self._get_profile_field("NAME", "display_name")

    @property
    def org_name(self) -> str | None:
        return self._get_profile_field("ORG_NAME", "org_name")
