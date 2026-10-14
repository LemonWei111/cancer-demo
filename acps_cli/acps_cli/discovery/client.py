"""Discovery 服务客户端：DSP 同步触发与查询验证。

错误分级策略：
    - DB 过滤查询失败 → DiscoveryError（ERROR，计入 FAIL_COUNT）
  - 语义查询失败   → 仅 log_warn（WARNING，不影响退出码）
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ─── 异常 ────────────────────────────────────────────────────────────────────


class DiscoveryError(Exception):
    """Discovery 操作失败（非预期错误或前置条件不满足）。"""


def log_info(message: str) -> None:
    """Emit info-level CLI log."""
    logger.info(message)


def log_warn(message: str) -> None:
    """Emit warning-level CLI log."""
    logger.warning(message)


# ─── HTTP 工具 ────────────────────────────────────────────────────────────────


def _request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> tuple[int, dict[str, Any] | None, str]:
    """Send HTTP request using httpx, return (status, parsed_json, raw_body)."""
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    try:
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
            resp = httpx.request(
                method,
                url,
                content=json.dumps(payload).encode(),
                headers=request_headers,
                timeout=timeout,
            )
        else:
            resp = httpx.request(method, url, headers=request_headers, timeout=timeout)
        status = resp.status_code
        raw_body = resp.text
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raw_body = exc.response.text
    except httpx.RequestError as exc:
        raise DiscoveryError(f"HTTP 请求失败: {method} {url} ({exc})") from exc

    parsed: dict[str, Any] | None = None
    if raw_body:
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError:
            parsed = None

    return status, parsed, raw_body


def _ensure(condition: bool, message: str) -> None:
    """Assert condition, raise DiscoveryError if false."""
    if not condition:
        raise DiscoveryError(message)


def _ensure_json_object(
    status: int,
    parsed: dict[str, Any] | None,
    raw: str,
    error_message: str,
) -> dict[str, Any]:
    """Ensure the response is a successful JSON object."""
    _ensure(status == 200 and isinstance(parsed, dict), f"{error_message}: {status} {raw}")
    assert isinstance(parsed, dict)
    return parsed


# ─── DSP 同步触发 ─────────────────────────────────────────────────────────────

_DSP_RESET_TIMEOUT = 30
_DSP_SYNC_TIMEOUT = 180
_DSP_SYNC_WAIT_TIMEOUT = 600
_DSP_SYNC_WAIT_INTERVAL = 5
_DSP_STATUS_TIMEOUT = 30
_DSP_CONTROL_TIMEOUT = 30
_DSP_REGISTRY_INFO_TIMEOUT = 30
_DSP_WEBHOOK_TIMEOUT = 30
_HEALTH_TIMEOUT = 5


def get_health_status(gateway_url: str, timeout: int = _HEALTH_TIMEOUT) -> int:
    """Return discovery health endpoint status code."""
    try:
        resp = httpx.get(f"{gateway_url}/health", timeout=timeout)
    except httpx.RequestError as exc:
        raise DiscoveryError(f"HTTP 请求失败: GET {gateway_url}/health ({exc})") from exc
    return resp.status_code


def run_dsp_action(
    gateway_url: str,
    action: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: int = _DSP_CONTROL_TIMEOUT,
) -> dict[str, Any]:
    """Run a DSP admin POST action and return the JSON response."""
    status, parsed, raw = _request_json(
        "POST",
        f"{gateway_url}/admin/dsp/{action}",
        payload=payload,
        timeout=timeout,
    )
    return _ensure_json_object(status, parsed, raw, f"DSP {action} 失败")


def get_dsp_status(
    gateway_url: str,
    *,
    min_acs_count: int | None = None,
) -> dict[str, Any]:
    """Fetch DSP status and optionally assert the minimum ACS count."""
    status, parsed, raw = _request_json("GET", f"{gateway_url}/admin/dsp/status", timeout=_DSP_STATUS_TIMEOUT)
    status_payload = _ensure_json_object(status, parsed, raw, "DSP status 失败")

    if min_acs_count is not None:
        counts = status_payload.get("object_count_by_type") or {}
        acs_count = int(counts.get("acs") or 0)
        _ensure(
            acs_count >= min_acs_count,
            f"DSP status 显示 ACS 对象不足: {status_payload}",
        )

    return status_payload


def wait_for_dsp_status(
    gateway_url: str,
    *,
    min_acs_count: int | None = None,
    previous_last_seq: int | None = None,
    previous_last_sync_time: Any = None,
    wait_timeout: int = _DSP_SYNC_WAIT_TIMEOUT,
    wait_interval: int = _DSP_SYNC_WAIT_INTERVAL,
) -> dict[str, Any]:
    """Poll DSP status until a triggered sync has completed."""

    deadline = time.monotonic() + wait_timeout
    last_status: dict[str, Any] | None = None
    last_error = ""
    saw_running = False

    while time.monotonic() < deadline:
        try:
            status_payload = get_dsp_status(gateway_url, min_acs_count=None)
            last_status = status_payload
        except DiscoveryError as exc:
            last_error = str(exc)
            time.sleep(wait_interval)
            continue

        counts = status_payload.get("object_count_by_type") or {}
        acs_count = int(counts.get("acs") or 0)
        min_count_ready = min_acs_count is None or acs_count >= min_acs_count
        manual_sync_error = str(status_payload.get("manual_sync_error") or "").strip()
        has_manual_sync_flag = "manual_sync_in_progress" in status_payload
        sync_in_progress = bool(status_payload.get("manual_sync_in_progress")) if has_manual_sync_flag else False
        if sync_in_progress:
            saw_running = True

        current_last_seq = status_payload.get("last_seq")
        current_last_sync_time = status_payload.get("last_sync_time")
        progress_observed = saw_running or (previous_last_seq is None and previous_last_sync_time is None)
        if previous_last_seq is not None and current_last_seq != previous_last_seq:
            progress_observed = True
        if previous_last_sync_time is not None and current_last_sync_time != previous_last_sync_time:
            progress_observed = True

        if manual_sync_error and not sync_in_progress:
            raise DiscoveryError(f"DSP sync 后台任务失败: {manual_sync_error}")

        sync_completed = (
            sync_in_progress is False
            and status_payload.get("needs_snapshot") is False
            and bool(current_last_sync_time)
            and progress_observed
        )
        if sync_completed:
            if min_count_ready:
                return status_payload
            raise DiscoveryError(f"DSP status 显示 ACS 对象不足: {status_payload}")

        time.sleep(wait_interval)

    raise DiscoveryError(
        f"DSP status 轮询超时: timeout={wait_timeout}s, last_status={last_status}, last_error={last_error}"
    )


def get_registry_info(gateway_url: str) -> dict[str, Any]:
    """Fetch connected registry information from discovery."""
    status, parsed, raw = _request_json(
        "GET",
        f"{gateway_url}/admin/dsp/registry-info",
        timeout=_DSP_REGISTRY_INFO_TIMEOUT,
    )
    return _ensure_json_object(status, parsed, raw, "DSP registry-info 失败")


def register_webhook(
    gateway_url: str,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Register a DSP webhook via discovery admin API."""
    status, parsed, raw = _request_json(
        "POST",
        f"{gateway_url}/admin/dsp/webhooks/register",
        payload=payload,
        headers=headers,
        timeout=_DSP_WEBHOOK_TIMEOUT,
    )
    return _ensure_json_object(status, parsed, raw, "DSP register-webhook 失败")


def trigger_sync(
    gateway_url: str,
    *,
    hard_reset: bool = True,
    min_acs_count: int | None = 1,
) -> dict[str, Any]:
    """触发 Discovery Server 的 DSP 数据同步。

    通过 Discovery Server 暴露的 HTTP 管理接口触发同步。

    Args:
        gateway_url: Discovery 网关基础 URL（已去除末尾斜杠）。

    Raises:
        DiscoveryError: HTTP 接口调用失败或同步未完成时抛出。
    """
    return _trigger_sync_via_gateway(
        gateway_url,
        hard_reset=hard_reset,
        min_acs_count=min_acs_count,
    )


def _trigger_sync_via_gateway(
    gateway_url: str,
    *,
    hard_reset: bool,
    min_acs_count: int | None,
) -> dict[str, Any]:
    """通过网关 admin API 触发 DSP 同步。"""
    if hard_reset:
        run_dsp_action(gateway_url, "hard-reset", timeout=_DSP_RESET_TIMEOUT)

    previous_status = get_dsp_status(gateway_url, min_acs_count=None)

    status, parsed, raw = _request_json(
        "POST",
        f"{gateway_url}/admin/dsp/sync",
        timeout=_DSP_SYNC_TIMEOUT,
    )
    if status == 504:
        log_warn("DSP sync 请求被网关以 504 超时返回，改为轮询 status 等待完成")
    else:
        _ensure_json_object(status, parsed, raw, "DSP sync 失败")

    status_payload = wait_for_dsp_status(
        gateway_url,
        min_acs_count=min_acs_count,
        previous_last_seq=previous_status.get("last_seq"),
        previous_last_sync_time=previous_status.get("last_sync_time"),
    )
    counts = status_payload.get("object_count_by_type") or {}
    log_info(f"DSP 同步完成，当前 ACS 对象数: {counts.get('acs', 0)}")
    return status_payload


# ─── 通用查询 ─────────────────────────────────────────────────────────────────


def query(
    gateway_url: str,
    payload: dict[str, Any],
    timeout: int = 90,
) -> dict[str, Any]:
    """向 discovery API 发送查询请求，返回原始结果。

    Args:
        gateway_url: Discovery 网关基础 URL。
        payload: 符合 ADP Discovery API 格式的查询 payload。
        timeout: 请求超时秒数。

    Returns:
        Discovery API 的原始 JSON 响应字典。

    Raises:
        DiscoveryError: HTTP 请求失败或响应非 JSON 时抛出。
    """
    discover_url = f"{gateway_url}/acps-adp-v2/discover"
    status, parsed, raw = _request_json("POST", discover_url, payload, timeout=timeout)
    _ensure(status == 200, f"Discovery 查询失败: {status} {raw}")
    _ensure(isinstance(parsed, dict), f"Discovery 查询返回非 JSON: {raw}")
    return parsed  # type: ignore[return-value]


# ─── 验证：DB 过滤查询（失败 = ERROR） ───────────────────────────────────────


def verify_filtered_query(
    gateway_url: str,
    aic: str,
    expected_active: bool,
) -> None:
    """通过数据库过滤查询验证 agent 可被 discovery 查询到。

    Args:
        gateway_url: Discovery 网关基础 URL。
        aic: 期望查询到的 agent AIC。
        expected_active: 期望该 agent 处于活跃状态。

    Raises:
        DiscoveryError: 查询失败或结果不符合预期时抛出。
    """
    discover_url = f"{gateway_url}/acps-adp-v2/discover"

    default_payload: dict[str, Any] = {
        "type": "filtered",
        "query": "",
        "limit": 5,
        "filter": {"conditions": [{"field": "aic", "op": "eq", "value": aic}]},
    }
    status, parsed, raw = _request_json("POST", discover_url, default_payload, timeout=90)
    _ensure(status == 200, f"Discovery 过滤查询失败: {status} {raw}")
    _ensure(isinstance(parsed, dict), f"Discovery 过滤查询返回非 JSON: {raw}")

    default_groups = (parsed.get("result") or {}).get("agents") or []  # type: ignore[union-attr]
    default_agents = (default_groups[0].get("agentSkills") or []) if default_groups else []

    if expected_active:
        _ensure(len(default_agents) >= 1, f"期望 active agent 未在过滤查询中找到: {parsed}")

        explicit_payload: dict[str, Any] = {
            "type": "filtered",
            "query": "",
            "limit": 5,
            "filter": {
                "conditions": [
                    {"field": "aic", "op": "eq", "value": aic},
                    {"field": "active", "op": "eq", "value": True},
                ]
            },
        }
        status, parsed, raw = _request_json("POST", discover_url, explicit_payload, timeout=90)
        _ensure(status == 200, f"Discovery active 过滤查询失败: {status} {raw}")
        _ensure(isinstance(parsed, dict), f"Discovery active 过滤查询返回非 JSON: {raw}")

        explicit_groups = (parsed.get("result") or {}).get("agents") or []  # type: ignore[union-attr]
        explicit_agents = (explicit_groups[0].get("agentSkills") or []) if explicit_groups else []
        _ensure(len(explicit_agents) >= 1, f"期望 active=true agent 未出现: {parsed}")

        acs_map: dict[str, Any] = (parsed.get("result") or {}).get("acsMap") or {}  # type: ignore[union-attr]
        acs_payload = acs_map.get(aic) or {}
        _ensure(acs_payload.get("active") is True, f"ACS active 字段不符预期: {acs_payload}")
    else:
        _ensure(
            len(default_agents) == 0,
            f"inactive agent 意外出现在过滤查询结果中: {parsed}",
        )


def wait_for_query_state(
    gateway_url: str,
    aic: str,
    expected_active: bool,
    timeout_seconds: int = 120,
    interval_seconds: int = 5,
) -> None:
    """轮询等待 discovery DB 过滤查询进入预期状态。

    Args:
        gateway_url: Discovery 网关基础 URL。
        aic: 期望查询到的 agent AIC。
        expected_active: 期望状态。
        timeout_seconds: 最大等待秒数（默认 120）。
        interval_seconds: 轮询间隔秒数（默认 5）。

    Raises:
        DiscoveryError: 超时后仍未满足预期时抛出。
    """
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            verify_filtered_query(gateway_url, aic, expected_active)
            return
        except DiscoveryError as exc:
            last_error = exc
            time.sleep(interval_seconds)

    raise DiscoveryError(f"discovery 过滤查询轮询超时（{timeout_seconds}s），最后错误: {last_error}")


# ─── 验证：语义查询（失败 = WARNING） ────────────────────────────────────────


def verify_semantic_query(gateway_url: str) -> list[str]:
    """执行语义查询验证（依赖 LLM），收集失败告警。

    Args:
        gateway_url: Discovery 网关基础 URL。

    Returns:
        告警消息列表（空列表表示全部通过）。
    """
    warnings: list[str] = []
    discover_url = f"{gateway_url}/acps-adp-v2/discover"
    test_queries = ["北京美食推荐", "酒店预订"]

    for q in test_queries:
        payload: dict[str, Any] = {"type": "explicit", "query": q, "limit": 5}
        try:
            status, parsed, raw = _request_json("POST", discover_url, payload, timeout=90)
            if status != 200:
                warnings.append(f"语义查询 [{q}] 返回 {status}: {raw[:200]}")
                continue
            if not isinstance(parsed, dict):
                warnings.append(f"语义查询 [{q}] 返回非 JSON")
                continue
            groups = (parsed.get("result") or {}).get("agents") or []
            agents = (groups[0].get("agentSkills") or []) if groups else []
            if len(agents) < 1:
                warnings.append(f"语义查询 [{q}] 结果为空")
            else:
                log_info(f"语义查询 [{q}] 通过，找到 {len(agents)} 个 agent")
        except DiscoveryError as exc:
            warnings.append(f"语义查询 [{q}] 失败: {exc}")

    return warnings
