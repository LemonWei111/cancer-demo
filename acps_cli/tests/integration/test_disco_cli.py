"""集成测试：Discovery 服务命令。

覆盖范围：
  - status：检查 discovery-server 健康状态
  - sync：触发 DSP 数据同步
    - query：查询已注册 Agent，包含结构化 request JSON 路径
    - dsp status / registry-info：验证新管理命令面可用
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from acps_cli.main import main

pytestmark = pytest.mark.integration


def _load_json_output(output: str) -> dict[str, object] | list[object]:
    """解析 CLI JSON 输出。"""
    return json.loads(output)


class TestDiscoCliStatus:
    """discover status 命令集成测试。"""

    def test_status_returns_ok(self, disco_conf: Path) -> None:
        """status 命令应可通过 --config 指定配置文件并返回 200 状态。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "discover", "status"],
        )

        assert result.exit_code == 0, f"status 失败，输出: {result.output}"
        assert "200" in result.output

    def test_status_via_server_url_override(self, disco_url: str) -> None:
        """status 命令应支持统一的 group 级 --server-url 和 --verbose。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--verbose", "discover", "--server-url", disco_url, "status"],
        )

        assert result.exit_code == 0, f"status via --server-url 失败，输出: {result.output}"
        assert "200" in result.output

    def test_status_unreachable_url_fails(self) -> None:
        """不可达的 server URL 应导致命令以非零退出码退出。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["discover", "--server-url", "http://localhost:19999", "status"],
        )

        assert result.exit_code != 0


class TestDiscoCliSync:
    """admin discovery run-sync 命令集成测试。"""

    def test_sync_attempts_connection(self, disco_conf: Path) -> None:
        """sync 命令应能正常调用并尝试联系 Discovery Server，不应抛出未处理异常。

        集成环境中 registry 可能没有已批准 Agent，sync 返回 500 是预期行为。
        此测试仅验证命令本身可以执行、不崩溃，业务逻辑完整验证在 e2e 测试中进行。
        """
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "admin", "discovery", "run-sync"],
        )

        # 不应有未处理异常（SystemExit 是正常的退出方式）
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            f"sync 产生未处理异常: {result.exception}, 输出: {result.output}"
        )
        # 命令应有输出（不论成功还是失败）
        assert result.output is not None


class TestDiscoCliQuery:
    """discover query 命令集成测试。"""

    def test_query_returns_json_array(self, disco_conf: Path) -> None:
        """query 命令应返回合法的 JSON 数组（即使结果为空）。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "discover", "query", "test-agent"],
        )

        assert result.exit_code == 0, f"query 失败，输出: {result.output}"
        try:
            data = _load_json_output(result.output)
        except json.JSONDecodeError:
            pytest.fail(f"query 输出不是合法 JSON: {result.output}")
        assert isinstance(data, (list, dict))

    def test_query_with_limit(self, disco_conf: Path) -> None:
        """query 命令应支持 --limit 参数。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "discover", "query", "agent", "--limit", "2"],
        )

        assert result.exit_code == 0, f"query --limit 失败，输出: {result.output}"
        data = _load_json_output(result.output)
        # 结果数量不超过 limit
        if isinstance(data, list):
            assert len(data) <= 2

    def test_query_supports_request_json(self, disco_conf: Path) -> None:
        """query 命令应支持以 --request-json 提交结构化 DiscoveryRequest。"""
        runner = CliRunner()

        request_payload = json.dumps(
            {
                "type": "explicit",
                "query": "test-agent",
                "limit": 1,
            },
            ensure_ascii=False,
        )

        result = runner.invoke(
            main,
            [
                "--config",
                str(disco_conf),
                "discover",
                "query",
                "--request-json",
                request_payload,
            ],
        )

        assert result.exit_code == 0, f"query --request-json 失败，输出: {result.output}"
        data = _load_json_output(result.output)
        assert isinstance(data, (list, dict))


class TestDiscoCliDsp:
    """admin discovery dsp 命令集成测试。"""

    def test_dsp_status_returns_json_object(self, disco_conf: Path) -> None:
        """dsp status 命令应返回合法 JSON 对象。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "admin", "discovery", "dsp", "status"],
        )

        assert result.exit_code == 0, f"dsp status 失败，输出: {result.output}"
        data = _load_json_output(result.output)
        assert isinstance(data, dict)

    def test_dsp_registry_info_returns_json_object(self, disco_conf: Path) -> None:
        """dsp registry-info 命令应返回合法 JSON 对象。"""
        runner = CliRunner()

        result = runner.invoke(
            main,
            ["--config", str(disco_conf), "admin", "discovery", "dsp", "registry-info"],
        )

        assert result.exit_code == 0, f"dsp registry-info 失败，输出: {result.output}"
        data = _load_json_output(result.output)
        assert isinstance(data, dict)
