"""单元测试 — admin mq CLI 命令（health / group / auth-probe）。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from acps_cli.main import main


def _write_mq_config(tmp_path: Path, extra: str = "") -> Path:
    """创建带 [mq] 节的最小 acps-cli.toml 供测试使用。"""
    config_path = tmp_path / "acps-cli.toml"
    config_path.write_text(
        '[mq]\ngroup_api_url = "https://localhost:9007"\nauth_api_url = "https://localhost:9008"\n' + extra,
        encoding="utf-8",
    )
    return config_path


def _write_certs(tmp_path: Path) -> tuple[Path, Path]:
    """写入假证书文件（内容无关，仅需存在）。"""
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.write_text("FAKE CERT")
    key.write_text("FAKE KEY")
    return cert, key


@pytest.mark.unit
class TestAdminMqHelp:
    """命令帮助文案冒烟测试。"""

    def test_admin_mq_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["admin", "mq", "--help"])
        assert result.exit_code == 0
        assert "--group-api-url" in result.output
        assert "--auth-api-url" in result.output
        assert "health" in result.output
        assert "group" in result.output
        assert "auth-probe" in result.output

    def test_admin_mq_health_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["admin", "mq", "health", "--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        assert "--cert-file" in result.output

    def test_admin_mq_group_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["admin", "mq", "group", "--help"])
        assert result.exit_code == 0
        assert "add-member" in result.output
        assert "remove-member" in result.output
        assert "delete" in result.output
        assert "kick" in result.output

    def test_admin_mq_auth_probe_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["admin", "mq", "auth-probe", "--help"])
        assert result.exit_code == 0
        assert "user" in result.output
        assert "vhost" in result.output
        assert "resource" in result.output
        assert "topic" in result.output


@pytest.mark.unit
class TestAdminMqHealth:
    def test_health_json_output_shape(self, tmp_path: Path) -> None:
        """health --json 输出结构必须包含 group_api 和 auth_api 键。"""
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        # mock MqAuthClient 返回 200
        with patch("acps_cli.mq.unified.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.get.return_value = (200, {"status": "ok"})
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                ["--config", str(config), "admin", "mq", "health", "--json"],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "group_api" in data
        assert "auth_api" in data

    def test_health_no_probe_cert_reports_error_in_json(self, tmp_path: Path) -> None:
        """未配置 probe 证书时，--json 输出中两个端点均应为 error。"""
        config = _write_mq_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["--config", str(config), "admin", "mq", "health", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["group_api"]["status"] == "error"
        assert data["auth_api"]["status"] == "error"

    def test_health_human_readable_output(self, tmp_path: Path) -> None:
        """不加 --json 时输出人类可读文本。"""
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.unified.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.get.return_value = (200, "")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                ["--config", str(config), "admin", "mq", "health"],
            )

        assert result.exit_code == 0
        assert "mq-auth-server health" in result.output
        assert "Group API" in result.output
        assert "Auth API" in result.output

    def test_health_uses_cli_api_url_overrides(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.unified.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.get.return_value = (200, "")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "--group-api-url",
                    "https://cli-group.example.com:9443",
                    "--auth-api-url",
                    "https://cli-auth.example.com:9444",
                    "health",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        assert [call.kwargs["base_url"] for call in mock_cls.call_args_list] == [
            "https://cli-group.example.com:9443",
            "https://cli-auth.example.com:9444",
        ]


@pytest.mark.unit
class TestAdminMqGroupAddMember:
    def test_add_member_success_json(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.group_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.put.return_value = (200, "")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "group",
                    "add-member",
                    "--leader-aic",
                    "LEADER-001",
                    "--group-id",
                    "GRP-001",
                    "--member-aic",
                    "MEMBER-001",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"
        assert data["leader_aic"] == "LEADER-001"
        assert data["group_id"] == "GRP-001"
        assert data["member_aic"] == "MEMBER-001"

    def test_add_member_http_error_json(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.group_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.put.return_value = (403, "Forbidden")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "group",
                    "add-member",
                    "--leader-aic",
                    "LEADER-001",
                    "--group-id",
                    "GRP-001",
                    "--member-aic",
                    "MEMBER-001",
                    "--json",
                ],
            )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "403" in data["message"]

    def test_add_member_no_cert_raises_error(self, tmp_path: Path) -> None:
        """未配置 group 证书时应输出错误。"""
        config = _write_mq_config(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(config),
                "admin",
                "mq",
                "group",
                "add-member",
                "--leader-aic",
                "LEADER-001",
                "--group-id",
                "GRP-001",
                "--member-aic",
                "MEMBER-001",
            ],
        )
        assert result.exit_code != 0

    def test_add_member_uses_cli_group_api_url_override(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.group_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.put.return_value = (200, "")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "--group-api-url",
                    "https://cli-group.example.com:9443",
                    "group",
                    "add-member",
                    "--leader-aic",
                    "LEADER-001",
                    "--group-id",
                    "GRP-001",
                    "--member-aic",
                    "MEMBER-001",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        assert mock_cls.call_args.kwargs["base_url"] == "https://cli-group.example.com:9443"


@pytest.mark.unit
class TestAdminMqGroupDelete:
    def test_delete_with_yes_flag(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.group_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.delete.return_value = (204, "")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "group",
                    "delete",
                    "--leader-aic",
                    "LEADER-001",
                    "--group-id",
                    "GRP-001",
                    "--yes",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_delete_without_yes_in_non_tty_exits_1(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "--config",
                str(config),
                "admin",
                "mq",
                "group",
                "delete",
                "--leader-aic",
                "LEADER-001",
                "--group-id",
                "GRP-001",
                "--json",
            ],
        )
        # CliRunner 默认非 TTY → 应以非零 exit 退出
        assert result.exit_code != 0


@pytest.mark.unit
class TestAdminMqGroupKick:
    def test_kick_502_reports_rabbitmq_unavailable(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'group_cert_file = "{cert}"\ngroup_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.group_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.delete.return_value = (502, "Bad Gateway")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "group",
                    "kick",
                    "--leader-aic",
                    "LEADER-001",
                    "--group-id",
                    "GRP-001",
                    "--member-aic",
                    "MEMBER-001",
                    "--json",
                ],
            )

        assert result.exit_code != 0
        data = json.loads(result.output)
        assert data["status"] == "error"
        assert "RabbitMQ Management" in data["message"]


@pytest.mark.unit
class TestAdminMqAuthProbeUser:
    def test_probe_user_allow(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.auth_probe_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.post_form.return_value = (200, "allow")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "auth-probe",
                    "user",
                    "--username",
                    "AIC-001",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "allow"
        assert data["username"] == "AIC-001"

    def test_probe_user_deny(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.auth_probe_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.post_form.return_value = (200, "deny")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "auth-probe",
                    "user",
                    "--username",
                    "invalid-user",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "deny"

    def test_probe_user_uses_cli_auth_api_url_override(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.auth_probe_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.post_form.return_value = (200, "allow")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "--auth-api-url",
                    "https://cli-auth.example.com:9444",
                    "auth-probe",
                    "user",
                    "--username",
                    "AIC-001",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        assert mock_cls.call_args.kwargs["base_url"] == "https://cli-auth.example.com:9444"


@pytest.mark.unit
class TestAdminMqAuthProbeResource:
    def test_probe_resource_json_schema(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.auth_probe_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.post_form.return_value = (200, "allow")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "auth-probe",
                    "resource",
                    "--username",
                    "AIC-001",
                    "--vhost",
                    "acps",
                    "--resource",
                    "queue",
                    "--name",
                    "inbox",
                    "--permission",
                    "read",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "allow"
        assert data["username"] == "AIC-001"
        assert data["resource"] == "queue"
        assert data["name"] == "inbox"
        assert data["permission"] == "read"


@pytest.mark.unit
class TestAdminMqAuthProbeTopic:
    def test_probe_topic_json_schema(self, tmp_path: Path) -> None:
        cert, key = _write_certs(tmp_path)
        config = _write_mq_config(
            tmp_path,
            f'probe_cert_file = "{cert}"\nprobe_key_file = "{key}"\n',
        )

        with patch("acps_cli.mq.auth_probe_cmd.MqAuthClient") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.post_form.return_value = (200, "allow")
            mock_cls.return_value = mock_instance

            runner = CliRunner()
            result = runner.invoke(
                main,
                [
                    "--config",
                    str(config),
                    "admin",
                    "mq",
                    "auth-probe",
                    "topic",
                    "--username",
                    "AIC-001",
                    "--vhost",
                    "acps",
                    "--name",
                    "amq.topic",
                    "--permission",
                    "write",
                    "--routing-key",
                    "acps.msg.AIC-001",
                    "--json",
                ],
            )

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["result"] == "allow"
        assert data["routing_key"] == "acps.msg.AIC-001"
