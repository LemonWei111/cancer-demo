"""Unified registry command groups used by the new acps-cli entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

import click

from acps_cli.shared.runtime import get_root_runtime
from acps_cli.shared.unified_config import build_registry_legacy_section

from . import admin_commands, commands
from .client import RegistryApiClient
from .config import CliOverrides, Config, ConfigError
from .exceptions import RegistryClientError
from .output import print_result

DEFAULT_USER_CREDENTIAL_FILE_NAME = "registry-user.json"
DEFAULT_ADMIN_CREDENTIAL_FILE_NAME = "registry-admin.json"


def _invoke_legacy_callback(command: click.Command, **kwargs: Any) -> None:
    callback = cast("Callable[..., None] | None", command.callback)
    if callback is None:
        raise click.ClickException(f"Command callback is missing for '{command.name}'.")
    callback(**kwargs)


def _build_registry_context(
    ctx: click.Context,
    *,
    server_url: str | None,
    mtls_url: str | None,
    credential_env_prefix: str,
    default_token_name: str,
    require_mtls: bool = False,
) -> None:
    runtime = get_root_runtime(ctx)
    admin = credential_env_prefix == "REGISTRY_ADMIN"
    legacy_section = build_registry_legacy_section(
        runtime,
        cli_base_url=server_url,
        cli_mtls_url=mtls_url,
        admin=admin,
        require_mtls=require_mtls,
    )
    try:
        config = Config(
            toml_section=legacy_section,
            overrides=CliOverrides(
                server_base_url=legacy_section["server_base_url"],
                mtls_base_url=legacy_section.get("mtls_base_url"),
                token_file=legacy_section["token_file"],
            ),
            credential_env_prefix=credential_env_prefix,
            default_token_name=default_token_name,
            config_file_dir=runtime.config_dir,
        )
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    ctx.obj = {
        "config": config,
        "client": RegistryApiClient(config),
    }


@click.group(name="auth", help="User authentication commands.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.pass_context
def auth_group(ctx: click.Context, server_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=None,
        credential_env_prefix="REGISTRY_USER",
        default_token_name=DEFAULT_USER_CREDENTIAL_FILE_NAME,
    )


@auth_group.command("login")
@click.option("--username", default=None, help="Username")
@click.option("--password", default=None, help="Password")
@click.option("--name", default=None, help="Display name for auto registration")
@click.option("--org-name", default=None, help="Organization name for auto registration")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def auth_login(
    ctx: click.Context,
    username: str | None,
    password: str | None,
    name: str | None,
    org_name: str | None,
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        commands.login,
        username=username,
        password=password,
        name=name,
        org_name=org_name,
        as_json=as_json,
    )


@auth_group.command("whoami")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def auth_whoami(ctx: click.Context, as_json: bool) -> None:
    _invoke_legacy_callback(commands.whoami, as_json=as_json)


@auth_group.command("change-password")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def auth_change_password(ctx: click.Context, as_json: bool) -> None:
    _invoke_legacy_callback(commands.change_password, as_json=as_json)


@click.group(name="agent", help="Manage Agent drafts and review lifecycle.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.pass_context
def agent_group(ctx: click.Context, server_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=None,
        credential_env_prefix="REGISTRY_USER",
        default_token_name=DEFAULT_USER_CREDENTIAL_FILE_NAME,
    )


@agent_group.command("list")
@click.option("--page", "page_num", default=1, type=int, show_default=True)
@click.option("--page-size", default=20, type=int, show_default=True)
@click.option("--status", "statuses", multiple=True, help="Filter statuses, can repeat")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_list(
    ctx: click.Context,
    page_num: int,
    page_size: int,
    statuses: tuple[str, ...],
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        commands.list_agents,
        page_num=page_num,
        page_size=page_size,
        statuses=statuses,
        as_json=as_json,
    )


@agent_group.command("save")
@click.option("--logo-url", default=None, help="Agent logo URL")
@click.option("--acs-file", required=True, help="Path to ACS JSON file")
@click.option("--ontology/--no-ontology", "is_ontology", default=False, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_save(
    ctx: click.Context,
    logo_url: str | None,
    acs_file: str,
    is_ontology: bool,
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        commands.upsert_agent,
        logo_url=logo_url,
        acs_file=acs_file,
        is_ontology=is_ontology,
        as_json=as_json,
    )


@agent_group.command("submit")
@click.option("--agent-id", required=True, help="Draft Agent UUID for manual approval")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_submit(ctx: click.Context, agent_id: str, as_json: bool) -> None:
    _invoke_legacy_callback(commands.submit_agent_for_approval, agent_id=agent_id, as_json=as_json)


@agent_group.command("check")
@click.option("--acs-file", required=True, help="Path to ACS JSON file")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_check(ctx: click.Context, acs_file: str, as_json: bool) -> None:
    _invoke_legacy_callback(commands.check_agent, acs_file=acs_file, as_json=as_json)


@agent_group.command("sync")
@click.option("--acs-file", required=True, help="Path to ACS JSON file")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_sync(ctx: click.Context, acs_file: str, as_json: bool) -> None:
    _invoke_legacy_callback(commands.sync_acs, acs_file=acs_file, as_json=as_json)


@agent_group.command("delete")
@click.option("--acs-file", required=True, help="Path to ACS JSON file")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def agent_delete(ctx: click.Context, acs_file: str, as_json: bool) -> None:
    _invoke_legacy_callback(commands.delete_agent, acs_file=acs_file, as_json=as_json)


@click.group(name="entity", help="Manage derived entities.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.option("--mtls-url", default=None, help="Override registry mTLS base URL.")
@click.pass_context
def entity_group(ctx: click.Context, server_url: str | None, mtls_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=mtls_url,
        credential_env_prefix="REGISTRY_USER",
        default_token_name=DEFAULT_USER_CREDENTIAL_FILE_NAME,
        require_mtls=True,
    )


@entity_group.command("derive")
@click.option(
    "--ontology-aic",
    required=True,
    help="Approved ontology AIC for derived entity registration",
)
@click.option("--payload-file", default=None, help="Path to derived entity payload JSON file")
@click.option(
    "--mtls-cert-file",
    type=click.Path(dir_okay=False),
    default=None,
    help="Override ontology mTLS certificate path",
)
@click.option(
    "--mtls-key-file",
    type=click.Path(dir_okay=False),
    default=None,
    help="Override ontology mTLS private key path",
)
@click.option(
    "--mtls-server-ca-file",
    type=click.Path(dir_okay=False),
    default=None,
    help="Override CA file used to verify the registry 9002 server certificate",
)
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def entity_derive(
    ctx: click.Context,
    ontology_aic: str,
    payload_file: str | None,
    mtls_cert_file: str | None,
    mtls_key_file: str | None,
    mtls_server_ca_file: str | None,
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        commands.register_entity,
        ontology_aic=ontology_aic,
        payload_file=payload_file,
        mtls_cert_file=mtls_cert_file,
        mtls_key_file=mtls_key_file,
        mtls_server_ca_file=mtls_server_ca_file,
        as_json=as_json,
    )


@click.group(name="eab", help="Manage external account binding credentials.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.pass_context
def cert_eab_group(ctx: click.Context, server_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=None,
        credential_env_prefix="REGISTRY_USER",
        default_token_name=DEFAULT_USER_CREDENTIAL_FILE_NAME,
    )


@cert_eab_group.command("fetch")
@click.option("--aic", required=True, help="Agent AIC")
@click.option("--output", "output_path", required=True, help="Output JSON file path")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def cert_eab_fetch(ctx: click.Context, aic: str, output_path: str, as_json: bool) -> None:
    _invoke_legacy_callback(commands.get_eab, aic=aic, output_path=output_path, as_json=as_json)


@click.group(name="auth", help="Registry administrator authentication commands.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.pass_context
def admin_auth_group(ctx: click.Context, server_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=None,
        credential_env_prefix="REGISTRY_ADMIN",
        default_token_name=DEFAULT_ADMIN_CREDENTIAL_FILE_NAME,
    )


@admin_auth_group.command("login")
@click.option("--username", default=None, help="Username")
@click.option("--password", default=None, help="Password")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_auth_login(ctx: click.Context, username: str | None, password: str | None, as_json: bool) -> None:
    _invoke_legacy_callback(admin_commands.login, username=username, password=password, as_json=as_json)


@admin_auth_group.command("whoami")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_auth_whoami(ctx: click.Context, as_json: bool) -> None:
    client: RegistryApiClient = ctx.obj["client"]
    try:
        result = client.whoami()
    except RegistryClientError as exc:
        raise click.ClickException(str(exc)) from exc
    print_result(result, as_json=as_json)


@admin_auth_group.command("change-password")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_auth_change_password(ctx: click.Context, as_json: bool) -> None:
    _invoke_legacy_callback(admin_commands.change_password, as_json=as_json)


@click.group(name="registry", help="Registry administration commands.")
@click.option("--server-url", default=None, help="Override registry server base URL.")
@click.pass_context
def admin_registry_group(ctx: click.Context, server_url: str | None) -> None:
    _build_registry_context(
        ctx,
        server_url=server_url,
        mtls_url=None,
        credential_env_prefix="REGISTRY_ADMIN",
        default_token_name=DEFAULT_ADMIN_CREDENTIAL_FILE_NAME,
    )


@admin_registry_group.group("review", help="Review submitted Agents.")
def admin_registry_review_group() -> None:
    return None


@admin_registry_review_group.command("list")
@click.option("--page", "page_num", default=1, type=int, show_default=True)
@click.option("--page-size", default=20, type=int, show_default=True)
@click.option("--status", "statuses", multiple=True, help="Filter statuses, can repeat")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_registry_review_list(
    ctx: click.Context,
    page_num: int,
    page_size: int,
    statuses: tuple[str, ...],
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        admin_commands.list_reviews,
        page_num=page_num,
        page_size=page_size,
        statuses=statuses,
        as_json=as_json,
    )


@admin_registry_review_group.command("approve")
@click.option("--agent-id", required=True, help="Agent UUID")
@click.option("--comments", default=None, help="Optional review comments")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_registry_review_approve(
    ctx: click.Context,
    agent_id: str,
    comments: str | None,
    as_json: bool,
) -> None:
    _invoke_legacy_callback(
        admin_commands.approve_review,
        agent_id=agent_id,
        comments=comments,
        as_json=as_json,
    )


@admin_registry_review_group.command("reject")
@click.option("--agent-id", required=True, help="Agent UUID")
@click.option("--comments", required=True, help="Reject reason")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_registry_review_reject(ctx: click.Context, agent_id: str, comments: str, as_json: bool) -> None:
    _invoke_legacy_callback(
        admin_commands.reject_review,
        agent_id=agent_id,
        comments=comments,
        as_json=as_json,
    )


@admin_registry_group.group("agent", help="Apply administrative Agent state changes.")
def admin_registry_agent_group() -> None:
    return None


@admin_registry_agent_group.command("disable")
@click.option("--agent-id", required=True, help="Agent UUID")
@click.option("--reason", default="Staff disable", show_default=True, help="Disable reason")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_registry_agent_disable(ctx: click.Context, agent_id: str, reason: str, as_json: bool) -> None:
    _invoke_legacy_callback(
        admin_commands.disable_agent,
        agent_id=agent_id,
        reason=reason,
        as_json=as_json,
    )


@admin_registry_agent_group.command("enable")
@click.option("--agent-id", required=True, help="Agent UUID")
@click.option("--json", "as_json", is_flag=True, help="Output JSON")
@click.pass_context
def admin_registry_agent_enable(ctx: click.Context, agent_id: str, as_json: bool) -> None:
    _invoke_legacy_callback(admin_commands.enable_agent, agent_id=agent_id, as_json=as_json)
