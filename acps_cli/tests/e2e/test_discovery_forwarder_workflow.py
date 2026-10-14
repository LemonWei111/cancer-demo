"""端到端测试：两实例 discovery single-forwarder 工作流。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from acps_cli.main import main as cli_main
from tests.e2e.conftest import DISCO_URL, REGISTRY_URL, make_acs_file
from tests.e2e.test_discovery_incremental_sync_workflow import (
    _prepare_agent_for_filtered_query,
    _wait_for_agent_in_discovery_database,
)
from tests.e2e.test_discovery_snapshot_sync_workflow import _login_admin, _login_user

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skip(
        reason=(
            "discovery-server 当前仅稳定支持单实例查询与 single-forwarder runtime；"
            "多实例 forwarder/fallback 联调待后续实现稳定后再启用"
        )
    ),
]

disco_main = cli_main
admin_main = cli_main
user_main = cli_main

_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_DISCOVERY_REPO = _WORKSPACE_ROOT / "discovery-server"
_SECONDARY_SCRIPT = _DISCOVERY_REPO / "scripts/local-dev/secondary-instance.sh"
_PRIMARY_PYTHON = _DISCOVERY_REPO / ".venv/bin/python"
_PRIMARY_URL = "http://localhost:9007"
_SECONDARY_URL = "http://localhost:9006"
_PRIMARY_HEALTH_URL = f"{_PRIMARY_URL}/acps-adp-v2/health"
_PRIMARY_FORWARDER_STATUS_URL = f"{_PRIMARY_URL}/acps-adp-v2/forwarder-status"
_SECONDARY_HEALTH_URL = f"{_SECONDARY_URL}/health"
_PRIMARY_LOG_FILE = _DISCOVERY_REPO / "logs/discovery-forwarder-primary.log"
_REGISTRY_DSP_BASE_URL = f"{REGISTRY_URL}/acps-dsp-v2"


def _load_discovery_env() -> dict[str, str]:
    env_path = _DISCOVERY_REPO / ".env"
    if not env_path.exists():
        pytest.skip(f"discovery-server 缺少 .env 文件: {env_path}")

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _tail_text(path: Path, max_lines: int = 80) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def _resolve_discovery_database_url(env_key: str) -> str:
    env_values = _load_discovery_env()
    database_url = str(env_values.get(env_key) or "").strip()
    if not database_url:
        pytest.fail(f"discovery-server/.env 缺少 {env_key}，无法为 forwarder e2e 预热 runtime availability")
    return database_url


def _run_secondary_command(action: str) -> None:
    if not _SECONDARY_SCRIPT.exists():
        pytest.skip(f"secondary instance 脚本不存在: {_SECONDARY_SCRIPT}")

    env_values = _load_discovery_env()
    secondary_database_url = env_values.get("TEST_DATABASE_URL")
    if not secondary_database_url:
        pytest.skip("discovery-server/.env 缺少 TEST_DATABASE_URL，无法启动 secondary forwarder fixture")

    env = os.environ.copy()
    env.update(
        {
            "DISCOVERY_SECONDARY_APP_ENV": "development",
            "DISCOVERY_SECONDARY_DATABASE_URL": secondary_database_url,
            "DISCOVERY_SECONDARY_DSP_BASE_URL": _REGISTRY_DSP_BASE_URL,
            "DSP_AUTO_START": "false",
            "DISCOVERY_SECONDARY_POLLING_SERVER_URL": "",
            "DISCOVERY_SECONDARY_FORWARDER_ENABLED": "false",
        }
    )

    result = subprocess.run(  # noqa: S603
        [str(_SECONDARY_SCRIPT), action],
        cwd=_DISCOVERY_REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"secondary instance {action} 失败:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _wait_for_http(url: str, *, expected_status: int = 200, timeout_seconds: int = 120) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=5)
            if response.status_code == expected_status:
                return
            last_error = f"status={response.status_code}, body={response.text}"
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as exc:
            last_error = str(exc)
        time.sleep(1)

    pytest.fail(f"服务未在预期时间内就绪: url={url}, last_error={last_error}")


def _wait_for_forwarder_health(expected_healthy: bool, *, timeout_seconds: int = 30) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.time() < deadline:
        response = httpx.get(_PRIMARY_FORWARDER_STATUS_URL, timeout=5)
        response.raise_for_status()
        payload = response.json()
        assert isinstance(payload, dict)
        last_payload = payload
        if payload.get("healthy") is expected_healthy:
            return payload
        time.sleep(1)

    pytest.fail(f"forwarder 健康状态未在预期时间内收敛: {last_payload}")


def _run_dsp_command(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    args: list[str],
) -> dict[str, object]:
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "admin",
            "discovery",
            "--server-url",
            server_url,
            *args,
        ],
    )
    assert result.exit_code == 0, f"{' '.join(args)} 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict), f"{' '.join(args)} 返回非 JSON 对象: {result.output}"
    return payload


def _run_dsp_control(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    action: str,
) -> dict[str, object]:
    payload = _run_dsp_command(
        runner,
        disco_conf=disco_conf,
        server_url=server_url,
        args=["dsp", action],
    )
    assert payload.get("success") is True, f"dsp {action} 未返回 success=true: {payload}"
    return payload


def _run_dsp_hard_reset(runner: CliRunner, *, disco_conf: Path, server_url: str) -> None:
    payload = _run_dsp_control(
        runner,
        disco_conf=disco_conf,
        server_url=server_url,
        action="hard-reset",
    )
    assert "硬重置" in str(payload.get("message") or ""), payload


def _run_snapshot_sync(runner: CliRunner, *, disco_conf: Path, server_url: str) -> dict[str, object]:
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "admin",
            "discovery",
            "--server-url",
            server_url,
            "run-sync",
            "--no-hard-reset",
        ],
    )
    assert result.exit_code == 0, f"snapshot sync 失败 ({server_url}): {result.output}"
    assert "Sync triggered successfully." in result.output
    return _run_dsp_command(
        runner,
        disco_conf=disco_conf,
        server_url=server_url,
        args=["dsp", "status"],
    )


def _run_snapshot_sync_until_agent_available(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    database_url: str,
    aic: str,
    max_attempts: int = 6,
    wait_timeout_seconds: int = 5,
) -> dict[str, object]:
    last_status: dict[str, object] | None = None
    for _ in range(max_attempts):
        last_status = _run_snapshot_sync(runner, disco_conf=disco_conf, server_url=server_url)
        if _wait_for_agent_in_discovery_database(
            database_url,
            aic,
            timeout_seconds=wait_timeout_seconds,
            fail_on_timeout=False,
        ):
            return last_status

    pytest.fail(
        "snapshot sync 未在预期时间内将目标 agent 写入 discovery database: "
        f"server_url={server_url}, aic={aic}, last_status={last_status}"
    )


def _run_filtered_query(runner: CliRunner, *, disco_conf: Path, server_url: str, aic: str) -> dict[str, object]:
    request_payload = json.dumps(
        {
            "type": "filtered",
            "query": "",
            "limit": 5,
            "filter": {
                "conditions": [
                    {"field": "aic", "op": "eq", "value": aic},
                    {"field": "active", "op": "eq", "value": True},
                ]
            },
        },
        ensure_ascii=False,
    )
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "discover",
            "--server-url",
            server_url,
            "query",
            "--request-json",
            request_payload,
        ],
    )
    assert result.exit_code == 0, f"filtered query 失败 ({server_url}, {aic}): {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _run_filtered_query_expect_failure(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    aic: str,
) -> str:
    request_payload = json.dumps(
        {
            "type": "filtered",
            "query": "",
            "limit": 5,
            "filter": {
                "conditions": [
                    {"field": "aic", "op": "eq", "value": aic},
                    {"field": "active", "op": "eq", "value": True},
                ]
            },
        },
        ensure_ascii=False,
    )
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "discover",
            "--server-url",
            server_url,
            "query",
            "--request-json",
            request_payload,
        ],
    )
    assert result.exit_code != 0, f"filtered query 应失败但实际成功: {result.output}"
    return result.output


def _wait_for_filtered_visibility(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    aic: str,
    expected_visible: bool,
) -> None:
    last_payload: dict[str, object] | None = None
    for _ in range(30):
        payload = _run_filtered_query(runner, disco_conf=disco_conf, server_url=server_url, aic=aic)
        last_payload = payload
        result = payload.get("result") or {}
        groups = result.get("agents") or []
        agent_count = sum(len(group.get("agentSkills") or []) for group in groups)
        if (agent_count >= 1) is expected_visible:
            return
        time.sleep(2)

    state = "可见" if expected_visible else "不可见"
    pytest.fail(f"filtered query 未在预期时间内进入{state}状态: {last_payload}")


def _wait_for_snapshot_ready(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
) -> dict[str, object]:
    last_payload: dict[str, object] | None = None
    for _ in range(30):
        payload = _run_dsp_command(
            runner,
            disco_conf=disco_conf,
            server_url=server_url,
            args=["dsp", "status"],
        )
        last_payload = payload
        object_counts = payload.get("object_count_by_type") or {}
        acs_count = object_counts.get("acs")
        if payload.get("needs_snapshot") is False and isinstance(acs_count, int) and acs_count >= 1:
            return payload
        time.sleep(2)

    pytest.fail(f"snapshot sync 未在预期时间内完成: {last_payload}")


def _run_explicit_query(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    query_text: str,
    target_aic: str | None = None,
) -> dict[str, object]:
    args = [
        "--config",
        str(disco_conf),
        "discover",
        "--server-url",
        server_url,
        "query",
    ]
    if target_aic is None:
        args.extend([query_text, "--type", "explicit", "--limit", "5"])
    else:
        request_payload = json.dumps(
            {
                "type": "explicit",
                "query": query_text,
                "limit": 5,
                "filter": {
                    "conditions": [
                        {"field": "aic", "op": "eq", "value": target_aic},
                        {"field": "active", "op": "eq", "value": True},
                    ]
                },
            },
            ensure_ascii=False,
        )
        args.extend(["--request-json", request_payload])

    result = runner.invoke(disco_main, args)
    assert result.exit_code == 0, f"explicit query 失败 ({server_url}, {query_text}): {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _assert_query_contains_aic(payload: dict[str, object], expected_aic: str) -> None:
    result = payload.get("result") or {}
    acs_map = result.get("acsMap") or {}
    groups = result.get("agents") or []
    agent_count = sum(len(group.get("agentSkills") or []) for group in groups)

    assert agent_count >= 1, f"query 未返回任何 agent: {payload}"
    assert expected_aic in acs_map, f"query 未命中目标 AIC: expected={expected_aic}, payload={payload}"


def _run_explicit_query_expect_failure(
    runner: CliRunner,
    *,
    disco_conf: Path,
    server_url: str,
    query_text: str,
    target_aic: str | None = None,
) -> str:
    args = [
        "--config",
        str(disco_conf),
        "discover",
        "--server-url",
        server_url,
        "query",
    ]
    if target_aic is None:
        args.extend([query_text, "--type", "explicit", "--limit", "5"])
    else:
        request_payload = json.dumps(
            {
                "type": "explicit",
                "query": query_text,
                "limit": 5,
                "filter": {
                    "conditions": [
                        {"field": "aic", "op": "eq", "value": target_aic},
                        {"field": "active", "op": "eq", "value": True},
                    ]
                },
            },
            ensure_ascii=False,
        )
        args.extend(["--request-json", request_payload])

    result = runner.invoke(disco_main, args)
    assert result.exit_code != 0, f"query 应失败但实际成功: {result.output}"
    return result.output


def _create_named_approved_agent(
    runner: CliRunner,
    *,
    work_dir: Path,
    reg_conf: Path,
    admin_conf: Path,
    label: str,
) -> tuple[str, str]:
    agent_name = f"forwarder-{label}-{uuid.uuid4().hex[:8]}"
    marker = f"phase4-forwarder-{label}-{uuid.uuid4().hex[:10]}"
    acs_path, _, _ = make_acs_file(work_dir, name=agent_name)
    payload = json.loads(acs_path.read_text(encoding="utf-8"))
    payload["description"] = f"{payload['description']} 唯一标识：{marker}。"
    skills = payload.get("skills") or []
    if skills:
        first_skill = skills[0]
        tags = list(first_skill.get("tags") or [])
        tags.append(marker)
        first_skill["tags"] = tags
        examples = list(first_skill.get("examples") or [])
        examples.append(marker)
        first_skill["examples"] = examples
    acs_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

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

    synced_acs = json.loads(acs_path.read_text(encoding="utf-8"))
    aic = str(synced_acs.get("aic") or "")
    assert aic, "审批后未获得 AIC"
    return aic, agent_name


@contextmanager
def _managed_secondary_instance() -> Iterator[None]:
    _run_secondary_command("stop")
    _run_secondary_command("start")
    _wait_for_http(_SECONDARY_HEALTH_URL)
    try:
        yield
    finally:
        _run_secondary_command("stop")


@contextmanager
def _managed_primary_forwarder_instance(*, fallback_to_local: bool) -> Iterator[None]:
    if not _PRIMARY_PYTHON.exists():
        pytest.skip(f"discovery-server Python 解释器不存在: {_PRIMARY_PYTHON}")

    env_values = _load_discovery_env()
    database_url = env_values.get("DATABASE_URL")
    if not database_url:
        pytest.skip("discovery-server/.env 缺少 DATABASE_URL，无法启动 primary forwarder probe")

    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": env_values.get("APP_ENV", "development"),
            "UVICORN_PORT": "9007",
            "DATABASE_URL": database_url,
            "DSP_BASE_URL": _REGISTRY_DSP_BASE_URL,
            "DSP_WEBHOOK_RECEIVE_URL": "http://localhost:9007/admin/dsp/webhooks/receive",
            "DSP_AUTO_START": "false",
            "POLLING_SERVER_URL": "",
            "FORWARDER_SERVER_ENABLED": "true",
            "FORWARDER_SERVER_URL": "http://localhost:9006/acps-adp-v2",
            "FORWARDER_FALLBACK_TO_LOCAL": "true" if fallback_to_local else "false",
            "FORWARDER_HEALTH_CHECK_INTERVAL": "1",
            "PYTHONPATH": str(_DISCOVERY_REPO),
        }
    )

    _PRIMARY_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PRIMARY_LOG_FILE.write_text("", encoding="utf-8")
    log_handle = _PRIMARY_LOG_FILE.open("a", encoding="utf-8")

    process = subprocess.Popen(  # noqa: S603
        [str(_PRIMARY_PYTHON), "-m", "app.main"],
        cwd=_DISCOVERY_REPO,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )

    try:
        _wait_for_http(_PRIMARY_HEALTH_URL)
        yield
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        log_handle.close()


class TestDiscoveryForwarderWorkflow:
    """验证 A -> B forward 与 fallback-to-local=true/false 的 live 行为。"""

    def test_single_forwarder_routes_to_secondary_and_honors_fallback_flag(
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
        runner = CliRunner()

        _login_user(runner, reg_conf, username, password)
        _login_admin(runner, admin_conf, admin_username, admin_password)

        with _managed_secondary_instance():
            with _managed_primary_forwarder_instance(fallback_to_local=False):
                _wait_for_forwarder_health(True)
                _run_dsp_control(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    action="stop",
                )
                _run_dsp_control(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_SECONDARY_URL,
                    action="stop",
                )
                _run_dsp_hard_reset(runner, disco_conf=disco_conf, server_url=_PRIMARY_URL)
                _run_dsp_hard_reset(runner, disco_conf=disco_conf, server_url=_SECONDARY_URL)

                secondary_aic, _secondary_query = _create_named_approved_agent(
                    runner,
                    work_dir=work_dir,
                    reg_conf=reg_conf,
                    admin_conf=admin_conf,
                    label="secondary",
                )
                secondary_database_url = _resolve_discovery_database_url("TEST_DATABASE_URL")
                _run_snapshot_sync_until_agent_available(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_SECONDARY_URL,
                    database_url=secondary_database_url,
                    aic=secondary_aic,
                )
                _wait_for_snapshot_ready(runner, disco_conf=disco_conf, server_url=_SECONDARY_URL)
                _prepare_agent_for_filtered_query(
                    secondary_database_url,
                    secondary_aic,
                    f"forwarder secondary seeded skill for {secondary_aic}",
                )
                _wait_for_filtered_visibility(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_SECONDARY_URL,
                    aic=secondary_aic,
                    expected_visible=True,
                )
                _wait_for_filtered_visibility(
                    runner,
                    disco_conf=disco_conf,
                    server_url=DISCO_URL,
                    aic=secondary_aic,
                    expected_visible=False,
                )

                before_forwarder_status = _wait_for_forwarder_health(True)
                forwarded_payload = _run_filtered_query(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    aic=secondary_aic,
                )
                _assert_query_contains_aic(forwarded_payload, secondary_aic)
                after_forwarder_status = _wait_for_forwarder_health(True)
                before_success = int((before_forwarder_status.get("stats") or {}).get("forwarder_success") or 0)
                after_success = int((after_forwarder_status.get("stats") or {}).get("forwarder_success") or 0)
                assert after_success >= before_success + 1, (
                    f"forwarder success 计数未推进: before={before_forwarder_status}, after={after_forwarder_status}"
                )

                primary_aic, _primary_query = _create_named_approved_agent(
                    runner,
                    work_dir=work_dir,
                    reg_conf=reg_conf,
                    admin_conf=admin_conf,
                    label="primary",
                )
                primary_database_url = _resolve_discovery_database_url("DATABASE_URL")
                _run_snapshot_sync_until_agent_available(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    database_url=primary_database_url,
                    aic=primary_aic,
                )
                _wait_for_snapshot_ready(runner, disco_conf=disco_conf, server_url=_PRIMARY_URL)
                _prepare_agent_for_filtered_query(
                    primary_database_url,
                    primary_aic,
                    f"forwarder primary seeded skill for {primary_aic}",
                )
                _wait_for_filtered_visibility(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    aic=primary_aic,
                    expected_visible=False,
                )
                _wait_for_filtered_visibility(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_SECONDARY_URL,
                    aic=primary_aic,
                    expected_visible=False,
                )
                _wait_for_filtered_visibility(
                    runner,
                    disco_conf=disco_conf,
                    server_url=DISCO_URL,
                    aic=primary_aic,
                    expected_visible=True,
                )

                primary_local_payload = _run_filtered_query(
                    runner,
                    disco_conf=disco_conf,
                    server_url=DISCO_URL,
                    aic=primary_aic,
                )
                _assert_query_contains_aic(primary_local_payload, primary_aic)

                _run_secondary_command("stop")
                unhealthy_status = _wait_for_forwarder_health(False)
                assert unhealthy_status.get("status") == "configured_but_unavailable"

                failed_output = _run_filtered_query_expect_failure(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    aic=primary_aic,
                )
                assert "503" in failed_output, failed_output
                assert "ForwarderUnavailable" in failed_output, failed_output

            with _managed_primary_forwarder_instance(fallback_to_local=True):
                unhealthy_status = _wait_for_forwarder_health(False)
                assert unhealthy_status.get("fallback_to_local") is True

                fallback_payload = _run_filtered_query(
                    runner,
                    disco_conf=disco_conf,
                    server_url=_PRIMARY_URL,
                    aic=primary_aic,
                )
                _assert_query_contains_aic(fallback_payload, primary_aic)

        if _PRIMARY_LOG_FILE.exists():
            log_tail = _tail_text(_PRIMARY_LOG_FILE)
            assert "Address already in use" not in log_tail, log_tail
