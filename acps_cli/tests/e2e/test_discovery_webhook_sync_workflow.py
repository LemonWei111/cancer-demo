"""端到端测试：Discovery webhook 推送同步工作流。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from acps_cli.main import main as cli_main
from tests.e2e.test_discovery_incremental_sync_workflow import (
    _create_approved_agent,
    _prepare_agent_for_filtered_query,
    _resolve_discovery_database_url,
    _run_dsp_status,
    _wait_for_filtered_query_state,
)
from tests.e2e.test_discovery_snapshot_sync_workflow import _login_admin, _login_user

pytestmark = pytest.mark.e2e

disco_main = cli_main

_WEBHOOK_SECRET = "test_123"
_DISCOVERY_WEBHOOK_SECRET_ENV = "ACPS_DISCOVERY_WEBHOOK_SECRET"
_DISCOVERY_SERVER_ENV_PATH = Path(__file__).resolve().parents[3] / "discovery-server" / ".env"


def _resolve_webhook_secret() -> str:
    """解析当前 discovery 运行时使用的 webhook secret。"""
    env_secret = os.getenv(_DISCOVERY_WEBHOOK_SECRET_ENV)
    if env_secret:
        return env_secret

    if _DISCOVERY_SERVER_ENV_PATH.exists():
        for line in _DISCOVERY_SERVER_ENV_PATH.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip() == "DSP_WEBHOOK_SECRET" and value.strip():
                return value.strip()

    return _WEBHOOK_SECRET


def _run_hard_reset(runner: CliRunner, disco_conf: Path) -> dict[str, object]:
    """执行 hard-reset，确保 webhook 场景从空状态起步。"""
    result = runner.invoke(
        disco_main,
        ["--config", str(disco_conf), "admin", "discovery", "dsp", "hard-reset"],
    )
    assert result.exit_code == 0, f"dsp hard-reset 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _register_webhook(
    runner: CliRunner,
    *,
    disco_conf: Path,
    disco_url: str,
) -> dict[str, object]:
    """通过 admin discovery dsp register-webhook 注册 webhook。"""
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "admin",
            "discovery",
            "dsp",
            "register-webhook",
            "--url",
            f"{disco_url}/admin/dsp/webhooks/receive",
            "--secret",
            _resolve_webhook_secret(),
        ],
    )
    assert result.exit_code == 0, f"register-webhook 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _wait_for_last_seq_advance(
    runner: CliRunner,
    *,
    disco_conf: Path,
    previous_last_seq: int | None,
) -> dict[str, object]:
    """轮询等待 webhook 触发 discovery 状态推进。"""
    last_payload: dict[str, object] | None = None
    for _ in range(20):
        payload = _run_dsp_status(runner, disco_conf)
        last_payload = payload
        last_seq = payload.get("last_seq")
        counts = payload.get("object_count_by_type") or {}
        acs_count = counts.get("acs")

        if isinstance(last_seq, int) and (previous_last_seq is None or last_seq > previous_last_seq):
            assert payload.get("needs_snapshot") is False, f"webhook 同步后不应继续要求 snapshot: {payload}"
            assert isinstance(acs_count, int) and acs_count >= 1, f"webhook 同步后 ACS 对象数异常: {payload}"
            return payload

        time.sleep(2)

    pytest.fail(f"webhook 未在预期时间内推进 discovery 状态: {last_payload}")


class TestDiscoveryWebhookSyncWorkflow:
    """验证 registry 变更可通过 webhook 推动 discovery 自动同步。"""

    def test_webhook_registration_drives_sync_without_manual_sync(
        self,
        work_dir: Path,
        reg_conf: Path,
        admin_conf: Path,
        disco_conf: Path,
        disco_url: str,
        user_credentials: tuple[str, str],
        admin_credentials: tuple[str, str],
    ) -> None:
        username, password = user_credentials
        admin_username, admin_password = admin_credentials
        discovery_database_url = _resolve_discovery_database_url("DATABASE_URL")
        runner = CliRunner()

        _login_user(runner, reg_conf, username, password)
        _login_admin(runner, admin_conf, admin_username, admin_password)

        _run_hard_reset(runner, disco_conf)
        before_status = _run_dsp_status(runner, disco_conf)
        before_last_seq = before_status.get("last_seq")
        assert before_status.get("needs_snapshot") is True, f"hard-reset 后状态不符合预期: {before_status}"

        webhook_payload = _register_webhook(
            runner,
            disco_conf=disco_conf,
            disco_url=disco_url,
        )
        assert webhook_payload["status"] == "active"
        assert webhook_payload["url"] == f"{disco_url}/admin/dsp/webhooks/receive"

        _login_admin(runner, admin_conf, admin_username, admin_password)

        _acs_path, _agent_id, aic = _create_approved_agent(
            runner,
            work_dir=work_dir,
            reg_conf=reg_conf,
            admin_conf=admin_conf,
        )

        after_status = _wait_for_last_seq_advance(
            runner,
            disco_conf=disco_conf,
            previous_last_seq=(before_last_seq if isinstance(before_last_seq, int) else None),
        )
        assert isinstance(after_status.get("last_seq"), int), f"webhook 同步后 last_seq 非法: {after_status}"
        assert after_status.get("needs_snapshot") is False, f"webhook 同步后不应继续要求 snapshot: {after_status}"
        _prepare_agent_for_filtered_query(
            discovery_database_url,
            aic,
            f"webhook sync seeded skill for {aic}",
        )
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=True,
        )
