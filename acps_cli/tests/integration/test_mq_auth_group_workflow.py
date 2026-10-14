"""集成测试 — mq-auth-server group 与 auth-probe 命令。

前置条件：
    测试夹具会自动启动 mq-auth-server（及 Redis）。
    如需手工联调，可预先启动：
        mq-auth-server → https://localhost:9007 / https://localhost:9008
    所有前置条件不足的情况由 conftest 夹具自动处理，不在测试中 skip。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.x509.oid import NameOID

from acps_cli.main import main


def _read_certificate_common_name(cert_path: Path) -> str:
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert common_names, f"证书缺少 CN: {cert_path}"
    value = common_names[0].value.strip()
    assert value, f"证书 CN 为空: {cert_path}"
    return value


# ─── 测试类 ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestMqHealthIntegration:
    def test_health_returns_ok_for_both_ports(self, mq_config_file: Path) -> None:
        """health --json 必须返回 group_api.status=ok 和 auth_api.status=ok。"""
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--config", str(mq_config_file), "admin", "mq", "health", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["group_api"]["status"] == "ok", data
        assert data["auth_api"]["status"] == "ok", data


@pytest.mark.integration
class TestMqGroupWorkflow:
    """group 子命令端到端工作流测试。"""

    GROUP_ID = "integ-group-20260101-abc123"
    MEMBER_AIC = "1.2.156.3088.1.1.89AB.123456.7LMNOP.1ABC"

    @pytest.fixture()
    def leader_aic(self, mq_cert_dir: Path) -> str:
        return _read_certificate_common_name(mq_cert_dir / "client.pem")

    def test_add_member_returns_success(self, mq_config_file: Path, leader_aic: str) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "group",
                "add-member",
                "--leader-aic",
                leader_aic,
                "--group-id",
                self.GROUP_ID,
                "--member-aic",
                self.MEMBER_AIC,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_remove_member_returns_success(self, mq_config_file: Path, leader_aic: str) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "group",
                "remove-member",
                "--leader-aic",
                leader_aic,
                "--group-id",
                self.GROUP_ID,
                "--member-aic",
                self.MEMBER_AIC,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_delete_group_returns_success(self, mq_config_file: Path, leader_aic: str) -> None:
        """使用 --yes 跳过交互确认。"""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "group",
                "delete",
                "--leader-aic",
                leader_aic,
                "--group-id",
                self.GROUP_ID,
                "--yes",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_kick_member_closes_connection(self, mq_config_file: Path, leader_aic: str) -> None:
        """kick 命令调用 RabbitMQ Management API。"""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "group",
                "kick",
                "--leader-aic",
                leader_aic,
                "--group-id",
                self.GROUP_ID,
                "--member-aic",
                self.MEMBER_AIC,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output


@pytest.mark.integration
class TestMqAuthProbeIntegration:
    """auth-probe 子命令集成测试。"""

    INVALID_USERNAME = "NONEXISTENT-001"
    VHOST = "acps"

    @pytest.fixture()
    def valid_username(self, mq_cert_dir: Path) -> str:
        return _read_certificate_common_name(mq_cert_dir / "client.pem")

    def test_probe_user_allow_for_valid_aic(self, mq_config_file: Path, valid_username: str) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "auth-probe",
                "user",
                "--username",
                valid_username,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["result"] in ("allow", "deny"), data

    def test_probe_user_deny_for_invalid_username(self, mq_config_file: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "auth-probe",
                "user",
                "--username",
                self.INVALID_USERNAME,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["result"] == "deny"

    def test_probe_vhost_allow_for_acps_vhost(self, mq_config_file: Path, valid_username: str) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "auth-probe",
                "vhost",
                "--username",
                valid_username,
                "--vhost",
                self.VHOST,
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["result"] in ("allow", "deny"), data

    def test_probe_resource_allow_for_inbox_queue(self, mq_config_file: Path, valid_username: str) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(mq_config_file),
                "admin",
                "mq",
                "auth-probe",
                "resource",
                "--username",
                valid_username,
                "--vhost",
                self.VHOST,
                "--resource",
                "queue",
                "--name",
                f"acps.inbox.{valid_username}",
                "--permission",
                "read",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["result"] in ("allow", "deny"), data
