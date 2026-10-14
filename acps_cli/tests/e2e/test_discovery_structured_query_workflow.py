"""端到端测试：discover 结构化 query 工作流。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from acps_cli.main import main as cli_main
from tests.e2e.test_discovery_incremental_sync_workflow import (
    _prepare_agent_for_filtered_query,
    _resolve_discovery_database_url,
)
from tests.e2e.test_discovery_snapshot_sync_workflow import (
    _demo_templates,
    _login_admin,
    _login_user,
    _prepare_demo_acs_copy,
    _register_and_approve_agent,
)

pytestmark = pytest.mark.e2e

disco_main = cli_main


def _run_sync(runner: CliRunner, disco_conf: Path) -> None:
    """执行一次快照同步，作为结构化查询的前置。"""
    result = runner.invoke(
        disco_main,
        ["--config", str(disco_conf), "admin", "discovery", "run-sync"],
    )
    assert result.exit_code == 0, f"disco sync 失败: {result.output}"
    assert "Sync triggered successfully." in result.output


def _run_query_with_request_file(
    runner: CliRunner,
    *,
    disco_conf: Path,
    request_file: Path,
) -> dict[str, object]:
    """通过 request-file 执行结构化查询。"""
    result = runner.invoke(
        disco_main,
        [
            "--config",
            str(disco_conf),
            "discover",
            "query",
            "--request-file",
            str(request_file),
        ],
    )
    assert result.exit_code == 0, f"query --request-file 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _run_filtered_query(
    runner: CliRunner,
    *,
    disco_conf: Path,
    aic: str,
) -> dict[str, object]:
    """通过 filter-json 执行结构化 filtered query。"""
    filter_payload = json.dumps(
        {
            "conditions": [
                {"field": "aic", "op": "eq", "value": aic},
                {"field": "active", "op": "eq", "value": True},
            ]
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
            "--type",
            "filtered",
            "--filter-json",
            filter_payload,
        ],
    )
    assert result.exit_code == 0, f"query --filter-json 失败: {result.output}"
    payload = json.loads(result.output)
    assert isinstance(payload, dict)
    return payload


def _assert_result_shape(payload: dict[str, object]) -> None:
    """断言 discovery 结果具备基础结构。"""
    result = payload.get("result")
    assert isinstance(result, dict), f"缺少 result 对象: {payload}"
    assert isinstance(result.get("acsMap") or {}, dict), f"缺少 acsMap: {payload}"
    assert isinstance(result.get("agents") or [], list), f"缺少 agents 列表: {payload}"
    assert isinstance(result.get("routes") or [], list), f"缺少 routes 列表: {payload}"


class TestDiscoveryStructuredQueryWorkflow:
    """验证 explicit / filtered / request-file 三种结构化查询入口。"""

    def test_structured_queries_hit_registered_demo_agent(
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

        template = _demo_templates()[0]
        acs_path, exact_query, _ = _prepare_demo_acs_copy(work_dir, template)
        agent = _register_and_approve_agent(
            runner,
            reg_conf,
            admin_conf,
            acs_path,
            exact_query=exact_query,
            intent_query=template.intent_query,
        )

        _run_sync(runner, disco_conf)
        _prepare_agent_for_filtered_query(
            discovery_database_url,
            agent.aic,
            f"structured query seeded skill for {agent.aic}",
        )

        filtered_payload = _run_filtered_query(
            runner,
            disco_conf=disco_conf,
            aic=agent.aic,
        )
        _assert_result_shape(filtered_payload)
        filtered_result = filtered_payload.get("result") or {}
        filtered_acs_map = filtered_result.get("acsMap") or {}
        assert agent.aic in filtered_acs_map, f"filtered query 未命中目标 AIC: {filtered_payload}"

        request_file = work_dir / "discovery-request.json"
        request_file.write_text(
            json.dumps(
                {
                    "type": "filtered",
                    "query": "",
                    "limit": 5,
                    "filter": {
                        "conditions": [
                            {"field": "aic", "op": "eq", "value": agent.aic},
                            {"field": "active", "op": "eq", "value": True},
                        ]
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        request_file_payload = _run_query_with_request_file(
            runner,
            disco_conf=disco_conf,
            request_file=request_file,
        )
        _assert_result_shape(request_file_payload)
        request_result = request_file_payload.get("result") or {}
        request_acs_map = request_result.get("acsMap") or {}
        assert agent.aic in request_acs_map, f"request-file query 未命中目标 AIC: {request_file_payload}"
