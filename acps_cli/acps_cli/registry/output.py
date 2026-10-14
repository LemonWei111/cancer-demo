"""CLI 输出辅助工具。"""

from __future__ import annotations

import json
from typing import Any

import click


def print_result(data: Any, as_json: bool) -> None:
    """按 JSON 或简单键值文本格式输出数据。"""
    if as_json:
        click.echo(json.dumps(data, ensure_ascii=True, indent=2))
        return

    if isinstance(data, dict):
        for key, value in data.items():
            click.echo(f"{key}: {value}")
        return

    click.echo(str(data))
