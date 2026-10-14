"""单元测试 — MqConfig 配置加载。"""

from __future__ import annotations

from pathlib import Path

import pytest

from acps_cli.mq.config import MqConfig


@pytest.mark.unit
class TestMqConfigDefaults:
    def test_defaults(self) -> None:
        cfg = MqConfig.from_toml({}, config_dir=None)
        assert cfg.group_api_url == "https://localhost:9007"
        assert cfg.auth_api_url == "https://localhost:9008"
        assert cfg.group_cert_file is None
        assert cfg.group_key_file is None
        assert cfg.probe_cert_file is None
        assert cfg.probe_key_file is None
        assert cfg.ca_cert_file is None
        assert cfg.timeout_seconds == 10

    def test_override_from_toml(self) -> None:
        cfg = MqConfig.from_toml(
            {
                "group_api_url": "https://mq.example.com:9007",
                "auth_api_url": "https://mq.example.com:9008",
                "timeout_seconds": 30,
            },
            config_dir=None,
        )
        assert cfg.group_api_url == "https://mq.example.com:9007"
        assert cfg.auth_api_url == "https://mq.example.com:9008"
        assert cfg.timeout_seconds == 30


@pytest.mark.unit
class TestMqConfigEnvVarPriority:
    def test_env_overrides_toml_group_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQ_GROUP_API_URL", "https://env.example.com:9007")
        cfg = MqConfig.from_toml({"group_api_url": "https://toml.example.com:9007"}, config_dir=None)
        assert cfg.group_api_url == "https://env.example.com:9007"

    def test_env_overrides_toml_auth_api_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQ_AUTH_API_URL", "https://env.example.com:9008")
        cfg = MqConfig.from_toml({"auth_api_url": "https://toml.example.com:9008"}, config_dir=None)
        assert cfg.auth_api_url == "https://env.example.com:9008"

    def test_cli_overrides_env_and_toml_api_urls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQ_GROUP_API_URL", "https://env.example.com:9007")
        monkeypatch.setenv("MQ_AUTH_API_URL", "https://env.example.com:9008")
        cfg = MqConfig.from_toml(
            {
                "group_api_url": "https://toml.example.com:9007",
                "auth_api_url": "https://toml.example.com:9008",
            },
            config_dir=None,
            cli_group_api_url="https://cli.example.com:9443",
            cli_auth_api_url="https://cli.example.com:9444",
        )
        assert cfg.group_api_url == "https://cli.example.com:9443"
        assert cfg.auth_api_url == "https://cli.example.com:9444"

    def test_env_overrides_cert_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        cert = tmp_path / "env.crt"
        key = tmp_path / "env.key"
        cert.touch()
        key.touch()
        monkeypatch.setenv("MQ_GROUP_CERT_FILE", str(cert))
        monkeypatch.setenv("MQ_GROUP_KEY_FILE", str(key))
        cfg = MqConfig.from_toml(
            {"group_cert_file": "/toml/cert.crt", "group_key_file": "/toml/cert.key"},
            config_dir=None,
        )
        assert cfg.group_cert_file == str(cert)
        assert cfg.group_key_file == str(key)

    def test_env_timeout_seconds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MQ_TIMEOUT_SECONDS", "25")
        cfg = MqConfig.from_toml({}, config_dir=None)
        assert cfg.timeout_seconds == 25

    def test_env_ca_cert_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        ca = tmp_path / "ca.pem"
        ca.touch()
        monkeypatch.setenv("MQ_CA_FILE", str(ca))
        cfg = MqConfig.from_toml({}, config_dir=None)
        assert cfg.ca_cert_file == str(ca)


@pytest.mark.unit
class TestMqConfigRelativePaths:
    def test_relative_cert_file_resolved_against_config_dir(self, tmp_path: Path) -> None:
        cert = tmp_path / "certs" / "leader.crt"
        cert.parent.mkdir()
        cert.touch()
        cfg = MqConfig.from_toml(
            {"group_cert_file": "certs/leader.crt"},
            config_dir=tmp_path,
        )
        assert cfg.group_cert_file == str(cert)

    def test_absolute_cert_file_unchanged(self, tmp_path: Path) -> None:
        cert = tmp_path / "abs.crt"
        cert.touch()
        cfg = MqConfig.from_toml(
            {"group_cert_file": str(cert)},
            config_dir=tmp_path,
        )
        assert cfg.group_cert_file == str(cert)

    def test_relative_path_without_config_dir_kept_as_is(self) -> None:
        cfg = MqConfig.from_toml(
            {"group_cert_file": "relative/path.crt"},
            config_dir=None,
        )
        # 无 config_dir 时相对路径保持原样（不拼接）
        assert cfg.group_cert_file == "relative/path.crt"

    def test_probe_cert_relative_resolved(self, tmp_path: Path) -> None:
        cert = tmp_path / "probe.crt"
        cert.touch()
        cfg = MqConfig.from_toml(
            {"probe_cert_file": "probe.crt"},
            config_dir=tmp_path,
        )
        assert cfg.probe_cert_file == str(cert)
