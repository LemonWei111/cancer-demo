from __future__ import annotations

import os
from pathlib import Path

from acps_cli.shared.config import load_toml_config


def test_load_toml_config_loads_env_from_config_dir(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "runtime"
    config_dir.mkdir()
    config_path = config_dir / "acps-cli.toml"
    config_path.write_text('[registry]\nbase_url = "http://localhost:9001"\n', encoding="utf-8")
    (config_dir / ".env").write_text(
        "REGISTRY_USER_USERNAME=runtime-user\n",
        encoding="utf-8",
    )

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    monkeypatch.delenv("REGISTRY_USER_USERNAME", raising=False)

    loaded, resolved = load_toml_config(str(config_path))

    assert resolved == config_path
    assert loaded["registry"]["base_url"] == "http://localhost:9001"
    assert os.environ["REGISTRY_USER_USERNAME"] == "runtime-user"
