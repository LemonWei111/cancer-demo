"""端到端测试：Discovery DSP 运行时控制工作流。"""

from __future__ import annotations

import json
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
    _run_snapshot_sync,
    _wait_for_filtered_query_state,
)
from tests.e2e.test_discovery_snapshot_sync_workflow import _login_admin, _login_user

pytestmark = pytest.mark.e2e

disco_main = cli_main


def _run_dsp_control(runner: CliRunner, disco_conf: Path, action: str) -> dict[str, object]:
    """执行一个 DSP 控制命令并返回 JSON 响应。"""
    result = runner.invoke(
        disco_main,
        ["--config", str(disco_conf), "admin", "discovery", "dsp", action],
    )
    assert result.exit_code == 0, f"dsp {action} 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict), f"dsp {action} 返回非 JSON 对象: {result.output}"
    assert payload.get("success") is True, f"dsp {action} 未返回 success=true: {payload}"
    return payload


def _wait_for_running_state(
    runner: CliRunner,
    *,
    disco_conf: Path,
    expected_running: bool,
) -> dict[str, object]:
    """轮询等待 DSP 进入预期运行状态。"""
    last_payload: dict[str, object] | None = None
    for _ in range(10):
        payload = _run_dsp_status(runner, disco_conf)
        last_payload = payload
        if payload.get("is_running") is expected_running:
            return payload
        time.sleep(1)

    state = "running" if expected_running else "stopped"
    pytest.fail(f"DSP 未在预期时间内进入 {state} 状态: {last_payload}")


def _assert_reset_state(payload: dict[str, object]) -> None:
    """断言 reset / hard-reset 后的状态字段已被清空。"""
    assert payload.get("last_seq") is None, f"reset 后 last_seq 应为 None: {payload}"
    assert payload.get("last_sync_time") is None, f"reset 后 last_sync_time 应为 None: {payload}"
    assert payload.get("needs_snapshot") is True, f"reset 后 needs_snapshot 应为 True: {payload}"
    assert payload.get("object_count_by_type") == {}, f"reset 后 object_count_by_type 应为空: {payload}"


class TestDiscoveryRuntimeControlWorkflow:
    """验证 `dsp start/stop/reset/hard-reset/status` 的真实运行效果。"""

    def test_runtime_controls_change_running_state_and_reset_semantics(
        self,
        work_dir: Path,
        reg_conf: Path,
        admin_conf: Path,
        disco_conf: Path,
        user_credentials: tuple[str, str],
        admin_credentials: tuple[str, str],
    ) -> None:
        username, password = user_credentials
        admin_username, admin_password = admin_credentials
        discovery_database_url = _resolve_discovery_database_url("DATABASE_URL")
        runner = CliRunner()

        _login_user(runner, reg_conf, username, password)
        _login_admin(runner, admin_conf, admin_username, admin_password)

        _run_dsp_control(runner, disco_conf, "start")
        initial_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=True,
        )
        assert initial_status.get("is_running") is True

        initial_stop_payload = _run_dsp_control(runner, disco_conf, "stop")
        assert "停止" in str(initial_stop_payload.get("message") or "")
        initial_stopped_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=False,
        )
        assert initial_stopped_status.get("is_running") is False

        hard_reset_payload = _run_dsp_control(runner, disco_conf, "hard-reset")
        assert "硬重置成功" in str(hard_reset_payload.get("message") or "")
        hard_reset_status = _run_dsp_status(runner, disco_conf)
        _assert_reset_state(hard_reset_status)

        post_reset_start_payload = _run_dsp_control(runner, disco_conf, "start")
        assert "启动" in str(post_reset_start_payload.get("message") or "")
        post_reset_started_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=True,
        )
        assert post_reset_started_status.get("is_running") is True

        _acs_path, _agent_id, aic = _create_approved_agent(
            runner,
            work_dir=work_dir,
            reg_conf=reg_conf,
            admin_conf=admin_conf,
        )

        synced_status = _run_snapshot_sync(runner, disco_conf)
        assert synced_status.get("is_running") is True
        assert isinstance(synced_status.get("last_seq"), int), f"snapshot 后 last_seq 非法: {synced_status}"
        counts = synced_status.get("object_count_by_type") or {}
        assert isinstance(counts.get("acs"), int) and counts["acs"] >= 1, f"snapshot 后 ACS 数量异常: {synced_status}"
        assert synced_status.get("needs_snapshot") is False, f"snapshot 后不应继续要求 snapshot: {synced_status}"
        _prepare_agent_for_filtered_query(
            discovery_database_url,
            aic,
            f"runtime control seeded skill for {aic}",
        )
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=True,
        )

        stop_payload = _run_dsp_control(runner, disco_conf, "stop")
        assert "停止" in str(stop_payload.get("message") or "")
        stopped_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=False,
        )
        assert stopped_status.get("is_running") is False

        start_payload = _run_dsp_control(runner, disco_conf, "start")
        assert "启动" in str(start_payload.get("message") or "")
        restarted_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=True,
        )
        assert restarted_status.get("is_running") is True

        pre_reset_stop_payload = _run_dsp_control(runner, disco_conf, "stop")
        assert "停止" in str(pre_reset_stop_payload.get("message") or "")
        pre_reset_stopped_status = _wait_for_running_state(
            runner,
            disco_conf=disco_conf,
            expected_running=False,
        )
        assert pre_reset_stopped_status.get("is_running") is False

        reset_payload = _run_dsp_control(runner, disco_conf, "reset")
        assert "重置" in str(reset_payload.get("message") or "")
        reset_status = _run_dsp_status(runner, disco_conf)
        _assert_reset_state(reset_status)
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=True,
        )

        final_hard_reset_payload = _run_dsp_control(runner, disco_conf, "hard-reset")
        assert "硬重置成功" in str(final_hard_reset_payload.get("message") or "")
        final_hard_reset_status = _run_dsp_status(runner, disco_conf)
        _assert_reset_state(final_hard_reset_status)
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=False,
        )
