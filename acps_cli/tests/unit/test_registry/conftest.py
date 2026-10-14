"""Unit test fixtures for registry CLI tests."""

from pathlib import Path

import pytest


@pytest.fixture()
def empty_conf(tmp_path: Path) -> Path:
    """创建一个最小化的 acps-cli.toml，供不需要真实配置值的单元测试使用。"""
    conf = tmp_path / "acps-cli.toml"
    conf.write_text(
        "[registry]\n"
        'base_url = "http://localhost:9001"\n'
        'mtls_base_url = "http://localhost:9002"\n\n'
        "[auth]\n"
        'user_token_file = "./.acps-cli/tokens/user.json"\n'
        'admin_token_file = "./.acps-cli/tokens/admin.json"\n',
        encoding="utf-8",
    )
    return conf
