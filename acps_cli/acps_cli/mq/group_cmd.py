"""group_cmd.py — admin mq group 子命令实现。"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from acps_cli.mq.client import MqAuthClient
from acps_cli.mq.config import MqConfig
from acps_cli.shared.runtime import get_root_runtime

# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

MQ_CLI_OVERRIDES_KEY = "mq_cli_overrides"


def _get_cli_url_override(ctx: click.Context, key: str) -> str | None:
    root_obj = ctx.find_root().obj
    if not isinstance(root_obj, dict):
        return None
    overrides = root_obj.get(MQ_CLI_OVERRIDES_KEY)
    if not isinstance(overrides, dict):
        return None
    value = overrides.get(key)
    return value if isinstance(value, str) and value else None


def _get_mq_config(ctx: click.Context) -> MqConfig:
    """从 RootCliRuntime.toml_data 加载 MqConfig。"""
    runtime = get_root_runtime(ctx)
    mq_section = runtime.toml_data.get("mq", {})
    if not isinstance(mq_section, dict):
        mq_section = {}
    return MqConfig.from_toml(
        mq_section,
        runtime.config_dir,
        cli_group_api_url=_get_cli_url_override(ctx, "group_api_url"),
        cli_auth_api_url=_get_cli_url_override(ctx, "auth_api_url"),
    )


def _get_group_client(
    cfg: MqConfig,
    cli_cert: str | None,
    cli_key: str | None,
) -> MqAuthClient:
    """构建 group 命令专用 mTLS 客户端（使用 Leader 证书）。

    优先级：CLI 参数 > 配置文件 group_cert_file。
    """
    cert = cli_cert or cfg.group_cert_file
    key = cli_key or cfg.group_key_file
    if not cert or not key:
        raise click.ClickException(
            "未找到 group 命令的 Leader 客户端证书。"
            "请通过 --cert-file / --key-file 指定，"
            "或在 [mq] 配置节设置 group_cert_file / group_key_file。"
        )
    return MqAuthClient(
        base_url=cfg.group_api_url,
        cert_file=cert,
        key_file=key,
        ca_cert_file=cfg.ca_cert_file,
        timeout=cfg.timeout_seconds,
    )


def _output_ok(output_json: bool, data: dict[str, Any]) -> None:
    """成功时输出：JSON 模式输出 JSON；人类可读模式输出 OK 摘要。"""
    if output_json:
        click.echo(json.dumps(data))
    else:
        click.echo("OK")


def _handle_response(
    status: int,
    body: object,
    output_json: bool,
    ok_data: dict[str, Any],
    op_desc: str,
) -> None:
    """统一处理 HTTP 响应：204/200 视为成功，其他报错。"""
    if status in (200, 204):
        _output_ok(output_json, ok_data)
        return
    detail = body if isinstance(body, str) else json.dumps(body)
    if output_json:
        click.echo(json.dumps({"status": "error", "message": f"HTTP {status}: {detail}"}))
        sys.exit(1)
    raise click.ClickException(f"{op_desc} 失败（HTTP {status}）：{detail}")


# ─── add-member ───────────────────────────────────────────────────────────────


@click.command(
    name="add-member",
    help="向群组添加成员（PUT /groups/{leader_aic}/{group_id}/members/{member_aic}）。",
)
@click.option("--leader-aic", required=True, help="Leader Agent AIC（与客户端证书 CN 一致）。")
@click.option("--group-id", required=True, help="群组 ID。")
@click.option("--member-aic", required=True, help="待添加成员 AIC。")
@click.option("--cert-file", default=None, help="Leader 客户端证书 PEM（覆盖配置）。")
@click.option("--key-file", default=None, help="Leader 客户端私钥 PEM（覆盖配置）。")
@click.option("--json", "output_json", is_flag=True, help="以 JSON 格式输出结果。")
@click.pass_context
def add_member(
    ctx: click.Context,
    leader_aic: str,
    group_id: str,
    member_aic: str,
    cert_file: str | None,
    key_file: str | None,
    output_json: bool,
) -> None:
    """向指定群组添加成员。"""
    cfg = _get_mq_config(ctx)
    client = _get_group_client(cfg, cert_file, key_file)
    path = f"/groups/{leader_aic}/{group_id}/members/{member_aic}"
    status, body = client.put(path)
    _handle_response(
        status,
        body,
        output_json,
        ok_data={
            "status": "ok",
            "leader_aic": leader_aic,
            "group_id": group_id,
            "member_aic": member_aic,
        },
        op_desc="添加成员",
    )


# ─── remove-member ────────────────────────────────────────────────────────────


@click.command(
    name="remove-member",
    help="从群组移除成员（DELETE /groups/{leader_aic}/{group_id}/members/{member_aic}）。",
)
@click.option("--leader-aic", required=True, help="Leader Agent AIC。")
@click.option("--group-id", required=True, help="群组 ID。")
@click.option("--member-aic", required=True, help="待移除成员 AIC。")
@click.option("--cert-file", default=None, help="Leader 客户端证书 PEM（覆盖配置）。")
@click.option("--key-file", default=None, help="Leader 客户端私钥 PEM（覆盖配置）。")
@click.option("--json", "output_json", is_flag=True, help="以 JSON 格式输出结果。")
@click.pass_context
def remove_member(
    ctx: click.Context,
    leader_aic: str,
    group_id: str,
    member_aic: str,
    cert_file: str | None,
    key_file: str | None,
    output_json: bool,
) -> None:
    """从指定群组移除成员。"""
    cfg = _get_mq_config(ctx)
    client = _get_group_client(cfg, cert_file, key_file)
    path = f"/groups/{leader_aic}/{group_id}/members/{member_aic}"
    status, body = client.delete(path)
    _handle_response(
        status,
        body,
        output_json,
        ok_data={
            "status": "ok",
            "leader_aic": leader_aic,
            "group_id": group_id,
            "member_aic": member_aic,
        },
        op_desc="移除成员",
    )


# ─── delete ───────────────────────────────────────────────────────────────────


@click.command(name="delete", help="删除整个群组 ACL（DELETE /groups/{leader_aic}/{group_id}）。")
@click.option("--leader-aic", required=True, help="Leader Agent AIC。")
@click.option("--group-id", required=True, help="群组 ID。")
@click.option("--yes", "confirmed", is_flag=True, help="跳过交互确认（适用于 CI/非 TTY 环境）。")
@click.option("--cert-file", default=None, help="Leader 客户端证书 PEM（覆盖配置）。")
@click.option("--key-file", default=None, help="Leader 客户端私钥 PEM（覆盖配置）。")
@click.option("--json", "output_json", is_flag=True, help="以 JSON 格式输出结果。")
@click.pass_context
def delete_group(
    ctx: click.Context,
    leader_aic: str,
    group_id: str,
    confirmed: bool,
    cert_file: str | None,
    key_file: str | None,
    output_json: bool,
) -> None:
    """删除整个群组 ACL（危险操作）。

    非 TTY 环境且未提供 --yes 时，自动取消并以 exit 1 退出。
    """
    if not confirmed:
        if not sys.stdin.isatty():
            if output_json:
                click.echo(
                    json.dumps(
                        {
                            "status": "error",
                            "message": "非 TTY 环境须提供 --yes 标志才能执行删除",
                        }
                    )
                )
                sys.exit(1)
            raise click.ClickException("非 TTY 环境须提供 --yes 标志才能执行删除。")
        prompt = f"即将删除群组 {group_id!r} 的所有 ACL。确认继续？[y/N] "
        answer = click.prompt(prompt, default="N", show_default=False)
        if answer.strip().lower() not in ("y", "yes"):
            click.echo("已取消。")
            return

    cfg = _get_mq_config(ctx)
    client = _get_group_client(cfg, cert_file, key_file)
    path = f"/groups/{leader_aic}/{group_id}"
    status, body = client.delete(path)
    _handle_response(
        status,
        body,
        output_json,
        ok_data={"status": "ok", "leader_aic": leader_aic, "group_id": group_id},
        op_desc="删除群组",
    )


# ─── kick ─────────────────────────────────────────────────────────────────────


@click.command(
    name="kick",
    help="强制断开成员连接（DELETE /groups/{leader_aic}/{group_id}/members/{member_aic}/connection）。",
)
@click.option("--leader-aic", required=True, help="Leader Agent AIC。")
@click.option("--group-id", required=True, help="群组 ID。")
@click.option("--member-aic", required=True, help="待踢出成员 AIC。")
@click.option("--cert-file", default=None, help="Leader 客户端证书 PEM（覆盖配置）。")
@click.option("--key-file", default=None, help="Leader 客户端私钥 PEM（覆盖配置）。")
@click.option("--json", "output_json", is_flag=True, help="以 JSON 格式输出结果。")
@click.pass_context
def kick_member(
    ctx: click.Context,
    leader_aic: str,
    group_id: str,
    member_aic: str,
    cert_file: str | None,
    key_file: str | None,
    output_json: bool,
) -> None:
    """强制断开指定成员与 RabbitMQ 的所有连接。

    若 mq-auth-server 无法访问 RabbitMQ Management API，返回 502/503；
    该状态不代表权限问题（区别于 403）。
    """
    cfg = _get_mq_config(ctx)
    client = _get_group_client(cfg, cert_file, key_file)
    path = f"/groups/{leader_aic}/{group_id}/members/{member_aic}/connection"
    status, body = client.delete(path)
    # kick 的特殊处理：5xx 单独提示（RabbitMQ Management 不可达）
    if status in (502, 503):
        detail = body if isinstance(body, str) else json.dumps(body)
        if output_json:
            click.echo(
                json.dumps(
                    {
                        "status": "error",
                        "message": f"RabbitMQ Management API 不可达（HTTP {status}）：{detail}",
                    }
                )
            )
            sys.exit(1)
        raise click.ClickException(
            f"RabbitMQ Management API 不可达（HTTP {status}）。"
            "请检查 mq-auth-server 与 RabbitMQ Management 的网络连通性。"
        )
    _handle_response(
        status,
        body,
        output_json,
        ok_data={
            "status": "ok",
            "leader_aic": leader_aic,
            "group_id": group_id,
            "member_aic": member_aic,
        },
        op_desc="踢出成员",
    )
