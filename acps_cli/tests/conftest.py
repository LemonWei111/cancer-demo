"""Top-level test configuration — global fixtures for all tests."""

import json

import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with standard subdirectory structure."""
    dirs = {
        "private": tmp_path / "private",
        "certs": tmp_path / "certs",
        "csr": tmp_path / "csr",
    }
    for d in dirs.values():
        d.mkdir()
    return tmp_path


@pytest.fixture
def sample_aic():
    """Return a valid test AIC."""
    return "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ"


@pytest.fixture
def config_file(tmp_workspace):
    """Generate a valid acps-cli.toml in the temp workspace, return its path."""
    conf_path = tmp_workspace / "acps-cli.toml"
    accounts_dir = tmp_workspace / "private" / "accounts"
    accounts_dir.mkdir(parents=True, exist_ok=True)
    conf_path.write_text(
        "[ca]\n"
        f'server_base_url = "http://localhost:8003"\n'
        f'account_keys_dir = "{accounts_dir}"\n'
        f'certs_dir = "{tmp_workspace / "certs"}"\n'
        f'private_keys_dir = "{tmp_workspace / "private"}"\n'
        f'csr_dir = "{tmp_workspace / "csr"}"\n'
        f'trust_bundle_path = "{tmp_workspace / "certs" / "trust-bundle.pem"}"\n',
        encoding="utf-8",
    )
    return str(conf_path)


@pytest.fixture
def eab_file(tmp_workspace):
    path = tmp_workspace / "eab.json"
    path.write_text(
        json.dumps(
            {
                "keyId": "kid-1",
                "macKey": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY",
                "aic": "1.2.156.3088.1.TEST.AAAAAA.BBBBBB.1.ZZZZ",
                "expiresAt": "2026-04-11T12:00:00+08:00",
            }
        ),
        encoding="utf-8",
    )
    return str(path)
