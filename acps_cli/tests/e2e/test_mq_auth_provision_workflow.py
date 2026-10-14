"""mq-auth-server E2E 测试。

约定：
    - 需要的全局依赖（Redis / RabbitMQ）由 just test bootstrap 预热。
    - 需要的本地服务（mq-auth-server）由 e2e fixture 自动托管启动。
    - 每个用例使用独立 group_id，避免顺序依赖。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from cryptography import x509
from cryptography.x509.oid import NameOID

from acps_cli.main import main

VALID_MEMBER_AIC = "1.2.156.3088.1.1.89AB.123456.7LMNOP.1ABC"
VALID_VHOST = "acps"


def _new_group_id() -> str:
    return f"e2e-group-{uuid.uuid4().hex[:8]}"


def _read_certificate_common_name(cert_path: Path) -> str:
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    common_names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert common_names, f"证书缺少 CN: {cert_path}"
    value = common_names[0].value.strip()
    assert value, f"证书 CN 为空: {cert_path}"
    return value


def _invoke_mq_json(config_file: Path, *args: str) -> dict[str, Any]:
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--config", str(config_file), "admin", "mq", *args, "--json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


@pytest.mark.e2e
class TestMqAuthProvisionWorkflow:
    """覆盖 mq CLI 的真实 e2e 工作流。"""

    @pytest.fixture()
    def leader_aic(self, mq_cert_dir: Path) -> str:
        return _read_certificate_common_name(mq_cert_dir / "client.pem")

    def test_phase1_prepare_local_mtls_assets(self, mq_config_file: Path, mq_cert_dir: Path) -> None:
        """Phase 1：测试夹具自动准备本地 mTLS 材料与配置文件。"""

        assert mq_config_file.is_file()
        for file_name in (
            "client.pem",
            "client.key",
            "acps-root-ca.pem",
        ):
            assert (mq_cert_dir / file_name).is_file()

        content = mq_config_file.read_text(encoding="utf-8")
        assert "[mq]" in content
        assert "group_api_url" in content
        assert "auth_api_url" in content

    def test_phase2_service_starts_and_health_ok(self, mq_config_file: Path) -> None:
        """Phase 2：mq-auth-server 自动启动后，两个 listener 都应健康。"""

        data = _invoke_mq_json(mq_config_file, "health")
        assert data["group_api"]["status"] == "ok", data
        assert data["auth_api"]["status"] == "ok", data

    def test_phase3_group_crud_lifecycle(self, mq_config_file: Path, leader_aic: str) -> None:
        """Phase 3：add-member / remove-member / delete 走通完整 ACL 生命周期。"""

        group_id = _new_group_id()
        group_queue = f"group_{leader_aic}_{group_id}_{VALID_MEMBER_AIC}"

        added = _invoke_mq_json(
            mq_config_file,
            "group",
            "add-member",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--member-aic",
            VALID_MEMBER_AIC,
        )
        assert added["status"] == "ok"

        allowed = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "resource",
            "--username",
            VALID_MEMBER_AIC,
            "--vhost",
            VALID_VHOST,
            "--resource",
            "queue",
            "--name",
            group_queue,
            "--permission",
            "read",
        )
        assert allowed["result"] == "allow", allowed

        removed = _invoke_mq_json(
            mq_config_file,
            "group",
            "remove-member",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--member-aic",
            VALID_MEMBER_AIC,
        )
        assert removed["status"] == "ok"

        denied = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "resource",
            "--username",
            VALID_MEMBER_AIC,
            "--vhost",
            VALID_VHOST,
            "--resource",
            "queue",
            "--name",
            group_queue,
            "--permission",
            "read",
        )
        assert denied["result"] == "deny", denied

        deleted = _invoke_mq_json(
            mq_config_file,
            "group",
            "delete",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--yes",
        )
        assert deleted["status"] == "ok"

    def test_phase4_auth_probe_decisions(self, mq_config_file: Path, leader_aic: str) -> None:
        """Phase 4：auth-probe 对稳定输入返回可预期决策。"""

        user_allow = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "user",
            "--username",
            leader_aic,
        )
        assert user_allow["result"] == "allow", user_allow

        user_deny = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "user",
            "--username",
            "NONEXISTENT-001",
        )
        assert user_deny["result"] == "deny", user_deny

        vhost_allow = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "vhost",
            "--username",
            leader_aic,
            "--vhost",
            VALID_VHOST,
        )
        assert vhost_allow["result"] == "allow", vhost_allow

        inbox_allow = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "resource",
            "--username",
            leader_aic,
            "--vhost",
            VALID_VHOST,
            "--resource",
            "queue",
            "--name",
            f"inbox_{leader_aic}",
            "--permission",
            "read",
        )
        assert inbox_allow["result"] == "allow", inbox_allow

        topic_allow = _invoke_mq_json(
            mq_config_file,
            "auth-probe",
            "topic",
            "--username",
            leader_aic,
            "--vhost",
            VALID_VHOST,
            "--name",
            "inbox.topic",
            "--permission",
            "read",
            "--routing-key",
            f"inbox_{leader_aic}",
        )
        assert topic_allow["result"] == "allow", topic_allow

    def test_phase5_kick_member_and_cleanup(self, mq_config_file: Path, leader_aic: str) -> None:
        """Phase 5：kick 走通 RabbitMQ Management 路径，并完成群组清理。"""

        group_id = _new_group_id()
        added = _invoke_mq_json(
            mq_config_file,
            "group",
            "add-member",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--member-aic",
            VALID_MEMBER_AIC,
        )
        assert added["status"] == "ok"

        kicked = _invoke_mq_json(
            mq_config_file,
            "group",
            "kick",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--member-aic",
            VALID_MEMBER_AIC,
        )
        assert kicked["status"] == "ok"

        deleted = _invoke_mq_json(
            mq_config_file,
            "group",
            "delete",
            "--leader-aic",
            leader_aic,
            "--group-id",
            group_id,
            "--yes",
        )
        assert deleted["status"] == "ok"
