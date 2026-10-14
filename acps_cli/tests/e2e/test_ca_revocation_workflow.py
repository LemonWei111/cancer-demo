"""端到端测试：Agent 删除后证书经 OCSP 状态查询变为 REVOKED。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from acps_cli.main import main as cli_main
from tests.e2e.conftest import make_acs_file

pytestmark = pytest.mark.e2e

user_main = cli_main
admin_main = cli_main
ca_main = cli_main


class TestCaOcspRevocationWorkflow:
    """验证删除 Agent 后证书经 OCSP 状态查询可观察到吊销状态。"""

    def test_delete_agent_revokes_certificate_via_ocsp(
        self,
        work_dir: Path,
        reg_conf: Path,
        admin_conf: Path,
        ca_conf: Path,
        user_credentials: tuple[str, str],
        admin_credentials: tuple[str, str],
    ) -> None:
        username, password = user_credentials
        admin_username, admin_password = admin_credentials
        runner = CliRunner()

        login_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "auth",
                "login",
                "--username",
                username,
                "--password",
                password,
                "--json",
            ],
        )
        assert login_result.exit_code == 0, f"login 失败: {login_result.output}"

        acs_path, _, _ = make_acs_file(work_dir)
        upsert_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "save",
                "--acs-file",
                str(acs_path),
                "--json",
            ],
        )
        assert upsert_result.exit_code == 0, f"upsert 失败: {upsert_result.output}"
        agent_id = json.loads(upsert_result.output)["agent_id"]

        submit_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "submit",
                "--agent-id",
                agent_id,
                "--json",
            ],
        )
        assert submit_result.exit_code == 0, f"submit 失败: {submit_result.output}"

        admin_login_result = runner.invoke(
            admin_main,
            [
                "--config",
                str(admin_conf),
                "admin",
                "auth",
                "login",
                "--username",
                admin_username,
                "--password",
                admin_password,
                "--json",
            ],
        )
        assert admin_login_result.exit_code == 0, f"admin login 失败: {admin_login_result.output}"

        approve_result = runner.invoke(
            admin_main,
            [
                "--config",
                str(admin_conf),
                "admin",
                "registry",
                "review",
                "approve",
                "--agent-id",
                agent_id,
                "--json",
            ],
        )
        assert approve_result.exit_code == 0, f"approve 失败: {approve_result.output}"
        approve_data = json.loads(approve_result.output)
        aic = approve_data.get("aic")
        assert aic, "审批后未获得 AIC"

        sync_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "sync",
                "--acs-file",
                str(acs_path),
                "--json",
            ],
        )
        assert sync_result.exit_code == 0, f"agent sync 失败: {sync_result.output}"

        eab_path = work_dir / "eab.json"
        eab_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "cert",
                "eab",
                "fetch",
                "--aic",
                aic,
                "--output",
                str(eab_path),
                "--json",
            ],
        )
        assert eab_result.exit_code == 0, f"cert eab fetch 失败: {eab_result.output}"

        cert_result = runner.invoke(
            ca_main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "issue",
                "--aic",
                aic,
                "--eab-file",
                str(eab_path),
                "--usage",
                "clientAuth",
            ],
        )
        assert cert_result.exit_code == 0, f"cert issue 失败: {cert_result.output}"

        good_result = runner.invoke(
            ca_main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "ocsp",
                "cert-status",
                "--aic",
                aic,
            ],
        )
        assert good_result.exit_code == 0, f"删除前 OCSP 检查失败: {good_result.output}"
        good_payload = json.loads(good_result.output)
        assert good_payload["certificateStatus"].upper() == "GOOD"

        delete_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "delete",
                "--acs-file",
                str(acs_path),
                "--json",
            ],
        )
        assert delete_result.exit_code == 0, f"delete 失败: {delete_result.output}"
        delete_payload = json.loads(delete_result.output)
        assert delete_payload["status"] == "deleted"

        revoked_payload = None
        for _ in range(15):
            ocsp_result = runner.invoke(
                ca_main,
                [
                    "--config",
                    str(ca_conf),
                    "cert",
                    "ocsp",
                    "cert-status",
                    "--aic",
                    aic,
                ],
            )
            assert ocsp_result.exit_code == 0, f"删除后 OCSP 检查失败: {ocsp_result.output}"
            ocsp_payload = json.loads(ocsp_result.output)
            if ocsp_payload["certificateStatus"].upper() == "REVOKED":
                revoked_payload = ocsp_payload
                break
            time.sleep(2)

        assert revoked_payload is not None, "删除 Agent 后 OCSP 未在预期时间内返回 REVOKED"
        assert revoked_payload["revocationTime"], "REVOKED 响应缺少 revocationTime"

    def test_disable_agent_revokes_certificate_via_ocsp(
        self,
        work_dir: Path,
        reg_conf: Path,
        admin_conf: Path,
        ca_conf: Path,
        user_credentials: tuple[str, str],
        admin_credentials: tuple[str, str],
    ) -> None:
        username, password = user_credentials
        admin_username, admin_password = admin_credentials
        disable_reason = "E2E disable"
        runner = CliRunner()

        login_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "auth",
                "login",
                "--username",
                username,
                "--password",
                password,
                "--json",
            ],
        )
        assert login_result.exit_code == 0, f"login 失败: {login_result.output}"

        acs_path, _, _ = make_acs_file(work_dir)
        upsert_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "save",
                "--acs-file",
                str(acs_path),
                "--json",
            ],
        )
        assert upsert_result.exit_code == 0, f"upsert 失败: {upsert_result.output}"
        agent_id = json.loads(upsert_result.output)["agent_id"]

        submit_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "agent",
                "submit",
                "--agent-id",
                agent_id,
                "--json",
            ],
        )
        assert submit_result.exit_code == 0, f"submit 失败: {submit_result.output}"

        admin_login_result = runner.invoke(
            admin_main,
            [
                "--config",
                str(admin_conf),
                "admin",
                "auth",
                "login",
                "--username",
                admin_username,
                "--password",
                admin_password,
                "--json",
            ],
        )
        assert admin_login_result.exit_code == 0, f"admin login 失败: {admin_login_result.output}"

        approve_result = runner.invoke(
            admin_main,
            [
                "--config",
                str(admin_conf),
                "admin",
                "registry",
                "review",
                "approve",
                "--agent-id",
                agent_id,
                "--json",
            ],
        )
        assert approve_result.exit_code == 0, f"approve 失败: {approve_result.output}"
        approve_data = json.loads(approve_result.output)
        aic = approve_data.get("aic")
        assert aic, "审批后未获得 AIC"

        eab_path = work_dir / "eab.json"
        eab_result = runner.invoke(
            user_main,
            [
                "--config",
                str(reg_conf),
                "cert",
                "eab",
                "fetch",
                "--aic",
                aic,
                "--output",
                str(eab_path),
                "--json",
            ],
        )
        assert eab_result.exit_code == 0, f"cert eab fetch 失败: {eab_result.output}"

        cert_result = runner.invoke(
            ca_main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "issue",
                "--aic",
                aic,
                "--eab-file",
                str(eab_path),
                "--usage",
                "clientAuth",
            ],
        )
        assert cert_result.exit_code == 0, f"cert issue 失败: {cert_result.output}"

        good_result = runner.invoke(
            ca_main,
            [
                "--config",
                str(ca_conf),
                "cert",
                "ocsp",
                "cert-status",
                "--aic",
                aic,
            ],
        )
        assert good_result.exit_code == 0, f"禁用前 OCSP 检查失败: {good_result.output}"
        good_payload = json.loads(good_result.output)
        assert good_payload["certificateStatus"].upper() == "GOOD"

        disable_result = runner.invoke(
            admin_main,
            [
                "--config",
                str(admin_conf),
                "admin",
                "registry",
                "agent",
                "disable",
                "--agent-id",
                agent_id,
                "--reason",
                disable_reason,
                "--json",
            ],
        )
        assert disable_result.exit_code == 0, f"disable 失败: {disable_result.output}"
        disable_payload = json.loads(disable_result.output)
        assert disable_payload["message"] == "Disabled"
        assert disable_payload["is_disabled"] is True
        assert disable_payload["disabled_reason"] == disable_reason

        revoked_payload = None
        for _ in range(15):
            ocsp_result = runner.invoke(
                ca_main,
                [
                    "--config",
                    str(ca_conf),
                    "cert",
                    "ocsp",
                    "cert-status",
                    "--aic",
                    aic,
                ],
            )
            assert ocsp_result.exit_code == 0, f"禁用后 OCSP 检查失败: {ocsp_result.output}"
            ocsp_payload = json.loads(ocsp_result.output)
            if ocsp_payload["certificateStatus"].upper() == "REVOKED":
                revoked_payload = ocsp_payload
                break
            time.sleep(2)

        assert revoked_payload is not None, "禁用 Agent 后 OCSP 未在预期时间内返回 REVOKED"
        assert revoked_payload["revocationTime"], "REVOKED 响应缺少 revocationTime"
