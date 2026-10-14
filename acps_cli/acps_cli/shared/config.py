"""统一 TOML 配置加载器。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from dotenv import load_dotenv


def find_config_file(config_path: str | None) -> Path | None:
    """按优先级查找配置文件：CLI 指定 → ./acps-cli.toml → ~/.acps-cli.toml。

    Args:
        config_path: CLI 显式指定的路径，None 表示自动搜索。

    Returns:
        找到的配置文件绝对路径，未找到返回 None。
    """
    if config_path:
        p = Path(config_path).expanduser().resolve()
        if not p.exists():
            print(f"错误：配置文件不存在: {config_path}", file=sys.stderr)
            sys.exit(2)
        return p
    cwd_candidate = Path.cwd() / "acps-cli.toml"
    if cwd_candidate.exists():
        return cwd_candidate
    home_candidate = Path.home() / ".acps-cli.toml"
    if home_candidate.exists():
        return home_candidate
    return None


def load_toml_config(config_path: str | None) -> tuple[dict[str, Any], Path | None]:
    """加载并解析 TOML 配置文件，同时加载 .env 文件。

    优先级：CLI 指定路径 → ./acps-cli.toml → ~/.acps-cli.toml。
    未找到任何配置文件时返回空 dict，不报错。

    Args:
        config_path: CLI 显式指定的配置文件路径。

    Returns:
        (配置 dict, 配置文件绝对路径 | None)
    """
    resolved = find_config_file(config_path)
    if resolved is not None:
        load_dotenv(resolved.parent / ".env", override=False)
    else:
        load_dotenv(override=False)
    if resolved is None:
        return {}, None
    try:
        with open(resolved, "rb") as f:
            return tomllib.load(f), resolved
    except Exception as exc:
        print(f"错误：配置文件解析失败 ({resolved}): {exc}", file=sys.stderr)
        sys.exit(2)
