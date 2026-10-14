"""单元测试 — Config 配置加载。"""

import pytest

from acps_cli.ca.config import CliOverrides, Config


@pytest.mark.unit
class TestConfigLoading:
    def test_load_from_toml_section(self):
        cfg = Config({"server_base_url": "http://localhost:8003"})
        assert cfg.ca_server_base_url == "http://localhost:8003"
        assert cfg.ca_server_atr_base_url == "http://localhost:8003/acps-atr-v2"
        assert cfg.ca_server_url == "http://localhost:8003/acps-atr-v2"

    def test_default_values(self):
        cfg = Config({"server_base_url": "http://localhost:8003"})
        assert cfg.account_keys_dir == "keyfiles/accounts"
        assert cfg.certs_dir == "keyfiles/certs"
        assert cfg.private_keys_dir == "keyfiles/private"
        assert cfg.csr_dir == "keyfiles/csr"
        assert cfg.trust_bundle_path == "keyfiles/trust-bundle.pem"

    def test_override_defaults(self):
        cfg = Config(
            {
                "server_base_url": "https://ca.example.com",
                "account_keys_dir": "/etc/keys/accounts",
                "certs_dir": "/etc/certs",
                "private_keys_dir": "/etc/private",
                "csr_dir": "/etc/csr",
                "trust_bundle_path": "/etc/certs/bundle.pem",
            }
        )
        assert cfg.account_keys_dir == "/etc/keys/accounts"
        assert cfg.certs_dir == "/etc/certs"
        assert cfg.private_keys_dir == "/etc/private"
        assert cfg.csr_dir == "/etc/csr"
        assert cfg.trust_bundle_path == "/etc/certs/bundle.pem"

    def test_account_key_path_for_aic(self):
        cfg = Config(
            {
                "server_base_url": "http://localhost:8003",
                "account_keys_dir": "/etc/accounts",
            }
        )
        aic = "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ"
        expected = "/etc/accounts/1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ.account.key"
        assert cfg.account_key_path_for(aic) == expected

    def test_account_key_path_for_default_dir(self):
        cfg = Config({"server_base_url": "http://localhost:8003"})
        aic = "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ"
        assert cfg.account_key_path_for(aic) == "keyfiles/accounts/1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ.account.key"

    def test_account_key_paths_are_scoped_per_aic(self):
        cfg = Config({"server_base_url": "http://localhost:8003"})
        assert cfg.account_key_path_for("AIC-001") != cfg.account_key_path_for("AIC-002")

    def test_empty_section_does_not_crash(self):
        cfg = Config({})
        assert cfg.account_keys_dir == "keyfiles/accounts"
        assert cfg.certs_dir == "keyfiles/certs"

    def test_env_var_overrides_toml(self, monkeypatch):
        monkeypatch.setenv("CA_CERTS_DIR", "/env/certs")
        cfg = Config({"server_base_url": "http://localhost:8003", "certs_dir": "/toml/certs"})
        assert cfg.certs_dir == "/env/certs"

    def test_cli_override_prefers_server_base_url(self):
        cfg = Config(
            {"server_base_url": "http://localhost:8003"},
            overrides=CliOverrides(server_base_url="http://localhost:9000/ca-server"),
        )
        assert cfg.ca_server_base_url == "http://localhost:9000/ca-server"
        assert cfg.ca_server_atr_base_url == "http://localhost:9000/ca-server/acps-atr-v2"

    def test_legacy_atr_root_input_is_normalized(self):
        cfg = Config({"server_base_url": "http://localhost:9000/ca-server/acps-atr-v2"})
        assert cfg.ca_server_base_url == "http://localhost:9000/ca-server"
        assert cfg.ca_server_atr_base_url == "http://localhost:9000/ca-server/acps-atr-v2"

    def test_admin_api_token_from_toml(self, monkeypatch):
        monkeypatch.delenv("CA_SERVER_ADMIN_API_TOKEN", raising=False)
        cfg = Config(
            {
                "server_base_url": "http://localhost:8003",
                "admin_api_token": "toml-token",
            }
        )
        assert cfg.admin_api_token == "toml-token"

    def test_admin_api_token_env_overrides_toml(self, monkeypatch):
        monkeypatch.setenv("CA_SERVER_ADMIN_API_TOKEN", "env-token")
        cfg = Config(
            {
                "server_base_url": "http://localhost:8003",
                "admin_api_token": "toml-token",
            }
        )
        assert cfg.admin_api_token == "env-token"


@pytest.mark.unit
class TestConfigValidation:
    def test_missing_ca_server_url_exits(self):
        cfg = Config({})
        with pytest.raises(SystemExit) as exc_info:
            _ = cfg.ca_server_base_url
        assert exc_info.value.code == 2, "缺少 server_base_url 应返回 EXIT_CONFIG_ERROR(2)"

    def test_invalid_url_exits(self):
        cfg = Config({"server_base_url": "not-a-url"})
        with pytest.raises(SystemExit) as exc_info:
            _ = cfg.ca_server_base_url
        assert exc_info.value.code == 2, "无效 URL 应返回 EXIT_CONFIG_ERROR(2)"
