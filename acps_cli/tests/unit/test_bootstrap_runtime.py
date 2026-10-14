from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_bootstrap_runtime_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "bootstrap_runtime.py"
    module_name = "tests.unit._bootstrap_runtime"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


bootstrap_runtime = _load_bootstrap_runtime_module()


def test_discover_demo_leader_runtime_reads_install_layout(tmp_path: Path) -> None:
    acs_path = tmp_path / "leader" / "atr" / "acs.json"
    acs_path.parent.mkdir(parents=True)
    (tmp_path / "leader" / "scenario" / "expert" / "tour").mkdir(parents=True)
    acs_path.write_text(
        json.dumps({"name": "旅游助理智能体", "aic": ""}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    runtime_spec = bootstrap_runtime.discover_demo_leader_runtime(tmp_path)

    assert runtime_spec.install_dir == tmp_path
    assert runtime_spec.leader_dir == tmp_path / "leader"
    assert runtime_spec.atr_dir == tmp_path / "leader" / "atr"
    assert runtime_spec.acs_path == acs_path
    assert runtime_spec.name == "旅游助理智能体"


def test_discover_demo_leader_runtime_requires_acs_json(tmp_path: Path) -> None:
    (tmp_path / "leader" / "atr").mkdir(parents=True)
    (tmp_path / "leader" / "scenario" / "expert" / "tour").mkdir(parents=True)

    with pytest.raises(bootstrap_runtime.BootstrapError, match="demo-leader ACS 文件"):
        bootstrap_runtime.discover_demo_leader_runtime(tmp_path)


def test_build_demo_leader_result_contains_runtime_file_paths(tmp_path: Path) -> None:
    runtime_spec = bootstrap_runtime.DemoLeaderRuntimeSpec(
        install_dir=tmp_path,
        leader_dir=tmp_path / "leader",
        scenario_dir=tmp_path / "leader" / "scenario" / "expert" / "tour",
        atr_dir=tmp_path / "leader" / "atr",
        acs_path=tmp_path / "leader" / "atr" / "acs.json",
        name="旅游助理智能体",
    )

    result = bootstrap_runtime.build_demo_leader_result(
        tmp_path / "bootstrap-artifacts" / "demo-leader",
        runtime_spec,
        "1.2.156.3088.1.1.TEST",
    )

    assert result["profile"] == "demo-leader"
    assert result["install_dir"] == str(tmp_path)
    assert result["leader_dir"] == str(tmp_path / "leader")
    assert result["aic"] == "1.2.156.3088.1.1.TEST"
    assert result["files"] == {
        "acs": str(tmp_path / "leader" / "atr" / "acs.json"),
        "client_cert": str(tmp_path / "leader" / "atr" / "client.pem"),
        "client_key": str(tmp_path / "leader" / "atr" / "client.key"),
        "trust_bundle": str(tmp_path / "leader" / "atr" / "trust-bundle.pem"),
    }


def test_build_parser_accepts_demo_leader_install_dir() -> None:
    parser = bootstrap_runtime.build_parser()

    args = parser.parse_args(["demo-leader", "--install-dir", "/opt/demo-leader"])

    assert args.command == "demo-leader"
    assert args.install_dir == "/opt/demo-leader"


def test_build_parser_accepts_rabbitmq_install_dir() -> None:
    parser = bootstrap_runtime.build_parser()

    args = parser.parse_args(["rabbitmq", "--install-dir", "/opt/rabbitmq"])

    assert args.command == "rabbitmq"
    assert args.install_dir == "/opt/rabbitmq"


def test_build_parser_accepts_redis_install_dir() -> None:
    parser = bootstrap_runtime.build_parser()

    args = parser.parse_args(["redis", "--install-dir", "/opt/redis"])

    assert args.command == "redis"
    assert args.install_dir == "/opt/redis"


@pytest.mark.parametrize(
    ("argv", "install_dir"),
    [
        (["rabbitmq", "--install-dir", "/opt/rabbitmq"], "/opt/rabbitmq"),
        (["redis", "--install-dir", "/opt/redis"], "/opt/redis"),
        (["demo-partner", "--install-dir", "/opt/demo-partner"], "/opt/demo-partner"),
        (["demo-leader", "--install-dir", "/opt/demo-leader"], "/opt/demo-leader"),
    ],
)
def test_resolve_server_install_dirs_ignores_missing_command_specific_attrs(
    argv: list[str],
    install_dir: str,
) -> None:
    parser = bootstrap_runtime.build_parser()
    args = parser.parse_args(argv)

    registry_install_dir, mq_auth_install_dir = bootstrap_runtime.resolve_server_install_dirs(args)

    assert registry_install_dir is None
    assert mq_auth_install_dir is None
    assert args.install_dir == install_dir


def test_build_rabbitmq_result_contains_infra_file_paths(tmp_path: Path) -> None:
    result = bootstrap_runtime.build_rabbitmq_result(
        tmp_path / "bootstrap-artifacts" / "rabbitmq",
        "1.2.156.3088.1.1.RABBITMQ",
    )

    assert result["profile"] == "rabbitmq"
    assert result["aic"] == "1.2.156.3088.1.1.RABBITMQ"
    assert result["files"] == {
        "server_cert": str(tmp_path / "bootstrap-artifacts" / "rabbitmq" / "rabbitmq-server.pem"),
        "server_key": str(tmp_path / "bootstrap-artifacts" / "rabbitmq" / "rabbitmq-server.key"),
        "client_cert": str(tmp_path / "bootstrap-artifacts" / "rabbitmq" / "rabbitmq-client.pem"),
        "client_key": str(tmp_path / "bootstrap-artifacts" / "rabbitmq" / "rabbitmq-client.key"),
        "ca_bundle": str(tmp_path / "bootstrap-artifacts" / "rabbitmq" / "acps-root-ca.pem"),
    }


def test_build_redis_result_contains_infra_file_paths(tmp_path: Path) -> None:
    result = bootstrap_runtime.build_redis_result(
        tmp_path / "bootstrap-artifacts" / "redis",
        "1.2.156.3088.1.1.REDIS",
    )

    assert result["profile"] == "redis"
    assert result["aic"] == "1.2.156.3088.1.1.REDIS"
    assert result["files"] == {
        "server_cert": str(tmp_path / "bootstrap-artifacts" / "redis" / "redis-server.pem"),
        "server_key": str(tmp_path / "bootstrap-artifacts" / "redis" / "redis-server.key"),
        "ca_bundle": str(tmp_path / "bootstrap-artifacts" / "redis" / "acps-root-ca.pem"),
    }
