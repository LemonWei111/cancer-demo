"""端到端测试：Discovery 增量同步工作流。"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from acps_cli.main import main as cli_main
from tests.e2e.conftest import REGISTRY_URL, make_acs_file

pytestmark = pytest.mark.e2e

disco_main = cli_main
admin_main = cli_main
user_main = cli_main
_DISCOVERY_REPO = Path(__file__).resolve().parents[3] / "discovery-server"
_MANAGED_DISCOVERY_DATABASE_URL_ENV = "ACPS_CLI_MANAGED_DISCOVERY_DATABASE_URL"
_DEFAULT_DISCOVERY_DATABASE_URL = "postgresql+asyncpg://discovery:discovery@localhost:5432/agent_discovery"
_DEFAULT_REGISTRY_STAFF_USERNAME = "staff"
_DEFAULT_REGISTRY_STAFF_PASSWORD = "staff123"
_MARK_RUNTIME_SCRIPT = "\n".join(
    (
        "from sqlalchemy import create_engine, text",
        "import sys",
        "database_url, aic, is_available_flag = sys.argv[1:4]",
        "if database_url.startswith('postgresql+asyncpg://'):",
        "    sync_database_url = database_url.replace('postgresql+asyncpg://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql://'):",
        "    sync_database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql+psycopg2://'):",
        "    sync_database_url = database_url",
        "else:",
        "    raise ValueError(f'Unsupported discovery database URL: {database_url}')",
        "engine = create_engine(sync_database_url, pool_pre_ping=True, future=True)",
        "with engine.begin() as connection:",
        "    connection.execute(text('DELETE FROM available_agents_runtime WHERE aic = :aic'), {'aic': aic})",
        "    connection.execute(",
        (
            "        text('INSERT INTO available_agents_runtime "
            "(aic, is_available, checked_at) VALUES (:aic, :is_available, NOW())'),"
        ),
        "        {'aic': aic, 'is_available': is_available_flag == 'true'},",
        "    )",
        "engine.dispose()",
    )
)


def _load_discovery_env() -> dict[str, str]:
    env_path = _DISCOVERY_REPO / ".env"
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _resolve_discovery_database_url(env_key: str = "DATABASE_URL") -> str:
    managed_database_url = os.getenv(_MANAGED_DISCOVERY_DATABASE_URL_ENV, "").strip()
    if managed_database_url:
        return managed_database_url

    database_url = str(_load_discovery_env().get(env_key) or _DEFAULT_DISCOVERY_DATABASE_URL).strip()
    if not database_url:
        pytest.fail(f"discovery-server 缺少 {env_key}，无法为 incremental sync e2e 预热 filtered query 数据")
    return database_url


_WAIT_FOR_AGENT_SCRIPT = "\n".join(
    (
        "from sqlalchemy import create_engine, text",
        "import sys",
        "import time",
        "database_url, aic, timeout_seconds = sys.argv[1:4]",
        "if database_url.startswith('postgresql+asyncpg://'):",
        "    sync_database_url = database_url.replace('postgresql+asyncpg://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql://'):",
        "    sync_database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql+psycopg2://'):",
        "    sync_database_url = database_url",
        "else:",
        "    raise ValueError(f'Unsupported discovery database URL: {database_url}')",
        "engine = create_engine(sync_database_url, pool_pre_ping=True, future=True)",
        "deadline = time.time() + float(timeout_seconds)",
        "while time.time() < deadline:",
        "    with engine.connect() as connection:",
        "        exists = connection.execute(text('SELECT 1 FROM agents WHERE aic = :aic'), {'aic': aic}).scalar()",
        "    if exists:",
        "        print('found')",
        "        break",
        "    time.sleep(1)",
        "else:",
        "    raise RuntimeError(f'Agent {aic} not found in discovery database before timeout')",
        "engine.dispose()",
    )
)
_PREPARE_FILTERED_QUERY_SCRIPT = "\n".join(
    (
        "from sqlalchemy import create_engine, delete, text",
        "import sys",
        "from sqlmodel import Session",
        "from app.core.config import settings",
        "from app.sync.model import Skill",
        "database_url, aic, description, skill_id = sys.argv[1:5]",
        "if database_url.startswith('postgresql+asyncpg://'):",
        "    sync_database_url = database_url.replace('postgresql+asyncpg://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql://'):",
        "    sync_database_url = database_url.replace('postgresql://', 'postgresql+psycopg2://', 1)",
        "elif database_url.startswith('postgresql+psycopg2://'):",
        "    sync_database_url = database_url",
        "else:",
        "    raise ValueError(f'Unsupported discovery database URL: {database_url}')",
        "engine = create_engine(sync_database_url, pool_pre_ping=True, future=True)",
        "with Session(engine) as session, session.begin():",
        "    session.exec(delete(Skill).where(Skill.aic == aic, Skill.skill_id == skill_id))",
        "    session.add(",
        "        Skill(",
        "            aic=aic,",
        "            skill_id=skill_id,",
        "            description=description,",
        "            embedding=[0.0] * settings.EMBEDDING_DIM,",
        "            sparse_embedding=None,",
        "        )",
        "    )",
        "engine.dispose()",
    )
)


def _mark_agent_runtime_availability(database_url: str, aic: str, *, is_available: bool = True) -> None:
    """为指定 AIC 预热 available_agents_runtime，可供 filtered 查询立即命中。"""

    python_executable = _DISCOVERY_REPO / ".venv/bin/python"
    if not python_executable.exists():
        pytest.fail(f"discovery-server Python 解释器不存在，无法预热 runtime availability: {python_executable}")

    result = subprocess.run(  # noqa: S603
        [
            str(python_executable),
            "-c",
            _MARK_RUNTIME_SCRIPT,
            database_url,
            aic,
            "true" if is_available else "false",
        ],
        cwd=_DISCOVERY_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"预热 discovery runtime availability 失败:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _wait_for_agent_in_discovery_database(
    database_url: str,
    aic: str,
    *,
    timeout_seconds: int = 30,
    fail_on_timeout: bool = True,
) -> bool:
    """等待目标 AIC 出现在 discovery agents 表。"""

    python_executable = _DISCOVERY_REPO / ".venv/bin/python"
    if not python_executable.exists():
        pytest.fail(f"discovery-server Python 解释器不存在，无法等待 agent 入库: {python_executable}")

    result = subprocess.run(  # noqa: S603
        [
            str(python_executable),
            "-c",
            _WAIT_FOR_AGENT_SCRIPT,
            database_url,
            aic,
            str(timeout_seconds),
        ],
        cwd=_DISCOVERY_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True
    if not fail_on_timeout:
        return False

    assert result.returncode == 0, f"等待 discovery agent 入库失败:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    return True


def _prepare_agent_for_filtered_query(database_url: str, aic: str, description: str) -> None:
    """为 filtered 查询准备最小可见性数据：runtime availability + 一条 skills 记录。"""

    python_executable = _DISCOVERY_REPO / ".venv/bin/python"
    if not python_executable.exists():
        pytest.fail(f"discovery-server Python 解释器不存在，无法准备 filtered 查询数据: {python_executable}")

    _wait_for_agent_in_discovery_database(database_url, aic)

    result = subprocess.run(  # noqa: S603
        [
            str(python_executable),
            "-c",
            _PREPARE_FILTERED_QUERY_SCRIPT,
            database_url,
            aic,
            description,
            "e2e-filtered-skill",
        ],
        cwd=_DISCOVERY_REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"准备 discovery filtered 查询数据失败:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def _login_user(runner: CliRunner, reg_conf: Path, username: str, password: str) -> None:
    """执行用户登录。"""
    result = runner.invoke(
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
    assert result.exit_code == 0, f"user login 失败: {result.output}"


def _login_admin(
    runner: CliRunner,
    admin_conf: Path,
    username: str,
    password: str,
) -> None:
    """执行管理员登录。"""
    result = runner.invoke(
        admin_main,
        [
            "--config",
            str(admin_conf),
            "admin",
            "auth",
            "login",
            "--username",
            username,
            "--password",
            password,
            "--json",
        ],
    )
    assert result.exit_code == 0, f"admin login 失败: {result.output}"


def _create_approved_agent(
    runner: CliRunner,
    *,
    work_dir: Path,
    reg_conf: Path,
    admin_conf: Path,
) -> tuple[Path, str, str]:
    """创建、提交并审批一个测试 Agent。"""
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
    if approve_result.exit_code != 0:
        if "401" not in approve_result.output:
            pytest.fail(f"approve 失败: {approve_result.output}")
        _approve_agent_via_staff_api(agent_id)

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
    return acs_path, agent_id, aic


def _approve_agent_via_staff_api(agent_id: str) -> None:
    """在测试用受管 registry 上，用 staff 凭证直接完成审批。"""

    username = os.getenv("REGISTRY_STAFF_USERNAME", _DEFAULT_REGISTRY_STAFF_USERNAME)
    password = os.getenv("REGISTRY_STAFF_PASSWORD", _DEFAULT_REGISTRY_STAFF_PASSWORD)
    login_response = httpx.post(
        f"{REGISTRY_URL}/api/v1/auth/login",
        data={"username": username, "password": password},
        timeout=5,
    )
    assert login_response.status_code == 200, f"staff login 失败: {login_response.text}"
    access_token = login_response.json().get("access_token")
    assert isinstance(access_token, str) and access_token, f"staff login 未返回 access_token: {login_response.text}"

    approve_response = httpx.post(
        f"{REGISTRY_URL}/api/v1/agent/staff/{agent_id}/process",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"approve": True, "comments": "approved via discovery e2e staff fallback"},
        timeout=5,
    )
    assert approve_response.status_code == 200, f"staff approve 失败: {approve_response.text}"


def _run_snapshot_sync(runner: CliRunner, disco_conf: Path) -> dict[str, object]:
    """执行一次完整快照同步。"""
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "admin",
            "discovery",
            "run-sync",
            "--no-hard-reset",
            "--skip-acs-check",
        ],
    )
    assert result.exit_code == 0, f"disco snapshot sync 失败: {result.output}"
    assert "Sync triggered successfully." in result.output
    return _run_dsp_status(runner, disco_conf)


def _run_hard_reset(runner: CliRunner, disco_conf: Path) -> dict[str, object]:
    """执行 hard-reset，确保增量同步场景从空状态起步。"""
    result = runner.invoke(
        disco_main,
        ["--config", str(disco_conf), "admin", "discovery", "dsp", "hard-reset"],
    )
    assert result.exit_code == 0, f"dsp hard-reset 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _run_snapshot_sync_until_agent_available(
    runner: CliRunner,
    disco_conf: Path,
    database_url: str,
    aic: str,
    *,
    max_attempts: int = 6,
    wait_timeout_seconds: int = 5,
) -> dict[str, object]:
    """重复触发 snapshot sync，直到目标 AIC 出现在 discovery agents 表。"""

    last_status: dict[str, object] | None = None
    for _ in range(max_attempts):
        last_status = _run_snapshot_sync(runner, disco_conf)
        if _wait_for_agent_in_discovery_database(
            database_url,
            aic,
            timeout_seconds=wait_timeout_seconds,
            fail_on_timeout=False,
        ):
            return last_status

    pytest.fail(
        f"snapshot sync 未在预期时间内将目标 agent 写入 discovery database: aic={aic}, last_status={last_status}"
    )


def _run_incremental_sync(runner: CliRunner, disco_conf: Path) -> dict[str, object]:
    """执行一次增量同步。"""
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "admin",
            "discovery",
            "run-sync",
            "--no-hard-reset",
            "--skip-acs-check",
        ],
    )
    assert result.exit_code == 0, f"disco incremental sync 失败: {result.output}"
    assert "Sync triggered successfully." in result.output
    return _run_dsp_status(runner, disco_conf)


def _run_dsp_status(runner: CliRunner, disco_conf: Path) -> dict[str, object]:
    """读取当前 DSP 状态。"""
    result = runner.invoke(
        disco_main,
        ["--config", str(disco_conf), "admin", "discovery", "dsp", "status"],
    )
    assert result.exit_code == 0, f"dsp status 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _run_filtered_query(
    runner: CliRunner,
    *,
    disco_conf: Path,
    aic: str,
    active_only: bool | None,
) -> dict[str, object]:
    """通过 discover query 提交结构化 filtered query。"""
    conditions: list[dict[str, object]] = [{"field": "aic", "op": "eq", "value": aic}]
    if active_only is not None:
        conditions.append({"field": "active", "op": "eq", "value": active_only})

    request_payload = json.dumps(
        {
            "type": "filtered",
            "query": "",
            "limit": 5,
            "filter": {"conditions": conditions},
        },
        ensure_ascii=False,
    )
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "discover",
            "query",
            "--request-json",
            request_payload,
        ],
    )
    assert result.exit_code == 0, f"filtered query 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _wait_for_filtered_query_state(
    runner: CliRunner,
    *,
    disco_conf: Path,
    aic: str,
    expected_visible: bool,
) -> dict[str, object]:
    """轮询等待 filtered query 进入预期可见性。"""
    last_payload: dict[str, object] | None = None
    for _ in range(15):
        payload = _run_filtered_query(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            active_only=None,
        )
        last_payload = payload
        result = payload.get("result") or {}
        groups = result.get("agents") or []
        agent_count = sum(len(group.get("agentSkills") or []) for group in groups)
        is_visible = agent_count >= 1

        if expected_visible and is_visible:
            active_payload = _run_filtered_query(
                runner,
                disco_conf=disco_conf,
                aic=aic,
                active_only=True,
            )
            active_result = active_payload.get("result") or {}
            active_acs_map = active_result.get("acsMap") or {}
            acs_payload = active_acs_map.get(aic) or {}
            assert acs_payload.get("active") is True, f"ACS active 字段不符预期: {active_payload}"
            return payload

        if not expected_visible and not is_visible:
            return payload

        time.sleep(2)

    state = "可见" if expected_visible else "不可见"
    pytest.fail(f"filtered query 未在预期时间内进入{state}状态: {last_payload}")


def _assert_last_seq_advanced(
    before_status: dict[str, object],
    after_status: dict[str, object],
) -> None:
    """断言增量同步推进了 DSP 序列。"""
    before_seq = before_status.get("last_seq")
    after_seq = after_status.get("last_seq")
    assert isinstance(before_seq, int), f"快照前 last_seq 非法: {before_status}"
    assert isinstance(after_seq, int), f"增量后 last_seq 非法: {after_status}"
    assert after_seq > before_seq, f"增量同步未推进 last_seq: before={before_seq}, after={after_seq}"
    assert after_status.get("needs_snapshot") is False, f"增量同步后不应重新退回 snapshot: {after_status}"


class TestDiscoveryIncrementalSyncWorkflow:
    """验证 disable/delete 变更可通过 `sync --no-hard-reset` 传播到 discovery。"""

    def test_incremental_sync_hides_disabled_agent(
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
        disable_reason = "discovery incremental disable"
        discovery_database_url = _resolve_discovery_database_url("DATABASE_URL")
        runner = CliRunner()

        _login_user(runner, reg_conf, username, password)
        _login_admin(runner, admin_conf, admin_username, admin_password)
        _run_hard_reset(runner, disco_conf)
        acs_path, agent_id, aic = _create_approved_agent(
            runner,
            work_dir=work_dir,
            reg_conf=reg_conf,
            admin_conf=admin_conf,
        )
        del acs_path

        before_status = _run_snapshot_sync_until_agent_available(
            runner,
            disco_conf,
            discovery_database_url,
            aic,
        )
        _prepare_agent_for_filtered_query(
            discovery_database_url,
            aic,
            f"incremental sync seeded skill for {aic}",
        )
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=True,
        )

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

        after_status = _run_incremental_sync(runner, disco_conf)
        _assert_last_seq_advanced(before_status, after_status)
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=False,
        )

    def test_incremental_sync_hides_deleted_agent(
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
        _run_hard_reset(runner, disco_conf)
        acs_path, _, aic = _create_approved_agent(
            runner,
            work_dir=work_dir,
            reg_conf=reg_conf,
            admin_conf=admin_conf,
        )

        before_status = _run_snapshot_sync_until_agent_available(
            runner,
            disco_conf,
            discovery_database_url,
            aic,
        )
        _prepare_agent_for_filtered_query(
            discovery_database_url,
            aic,
            f"incremental sync seeded skill for {aic}",
        )
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=True,
        )

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

        after_status = _run_incremental_sync(runner, disco_conf)
        _assert_last_seq_advanced(before_status, after_status)
        _wait_for_filtered_query_state(
            runner,
            disco_conf=disco_conf,
            aic=aic,
            expected_visible=False,
        )
