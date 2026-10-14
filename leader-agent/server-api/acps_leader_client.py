# leader-agent/server-api/acps_leader_client.py
"""
Leader 侧 ACPs 客户端：通过 AIP 协议调用子智能体。

本次改动：
- `_open_group_impl` 对 pending invitation 做多次重试（SDK 有时不清）
- `_recent_dissolves` 记录最近解散时间，短时间内避免重复邀请同一 partner
- `GroupCtx.pending_partners` 让上层知道谁没进来
- 其他：退避轮询、统一 access 上报
"""
import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

from acps_sdk.aip import (
    ACSObject, FileDataItem, GroupLeader, TaskCommandType,
    TaskState, TextDataItem,
)
from acps_sdk.aip.aip_group_leader import PartnerInviteError
from acps_sdk.amp import (
    AccessBody, AccessRequest, AccessResponse, AccessParticipant, ErrorInfo,
)

from acps_config import (
    build_mq_ssl_context, AMP_LOG_DIR, DISCOVERY_URL,
    HEARTBEAT_INTERVAL, is_identity_binding_enabled,
)
from acps_emitters import (
    KafkaHeartbeatEmitter, KafkaAccessEmitter, KafkaMessageEmitter,
    get_forwarder,
)

logger = logging.getLogger("AcpsLeaderClient")

# 轮询等待退避参数
_WAIT_INITIAL = 0.1
_WAIT_MAX = 1.0
_WAIT_FACTOR = 1.5

# pending invitation 重试策略
_PENDING_RETRY_ATTEMPTS = 3
_PENDING_RETRY_BASE_DELAY = 2.0   # 秒，每次递增 2s

# 同一 partner 短时间内不重复邀请的冷却窗口
_DISSOLVE_COOLDOWN = 30.0   # 秒


def _guess_mime(path: str) -> str:
    low = path.lower()
    if low.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if low.endswith((".tif", ".tiff")):
        return "image/tiff"
    if low.endswith((".svs", ".ndpi", ".mrxs", ".wsi")):
        return "application/octet-stream"
    return "image/png"


@dataclass
class Job:
    agent_aic: str
    image_path: Optional[str] = None
    data_items: Optional[list] = None
    text_hint: str = ""


@dataclass
class GroupCtx:
    session_id: str
    session: Any
    agent_aics: List[str]
    # ★ 本轮没成功加入群组的 partner
    pending_partners: Set[str] = field(default_factory=set)

    def joined_aics(self) -> Set[str]:
        return set(self.agent_aics) - self.pending_partners


class AcpsLeaderClient:
    def __init__(self, leader_aic: str, mq_config: dict,
                 amp_log_dir: Optional[str] = None):
        self.leader_aic = leader_aic
        self.mq_config = mq_config
        self.ssl_ctx = build_mq_ssl_context()
        self.leader: Optional[GroupLeader] = None
        self._heartbeat_task: Optional[asyncio.Task] = None

        # ★ partner_aic -> dissolve 时间戳（monotonic）
        self._recent_dissolves: Dict[str, float] = {}

        amp_dir = Path(amp_log_dir or str(AMP_LOG_DIR))
        amp_dir.mkdir(parents=True, exist_ok=True)
        get_forwarder()

        self.heartbeat_emitter = KafkaHeartbeatEmitter(
            log_file=amp_dir / f"amp_heartbeat_{leader_aic}.jsonl", aic=leader_aic,
        )
        self.access_emitter = KafkaAccessEmitter(
            log_file=amp_dir / f"amp_access_{leader_aic}.jsonl", aic=leader_aic,
        )
        self.message_emitter = KafkaMessageEmitter(
            log_file=amp_dir / f"amp_message_{leader_aic}.jsonl", aic=leader_aic,
        )

    async def start(self):
        logger.info("[start] AcpsLeaderClient 启动，pid=%s AIC=%s",
                    os.getpid(), self.leader_aic)

        binding_enabled = is_identity_binding_enabled()
        logger.info(
            "[start] identity_binding_enabled=%s (env ACPS_IDENTITY_BINDING=%r)",
            binding_enabled, os.getenv("ACPS_IDENTITY_BINDING"),
        )

        self.leader = GroupLeader(
            leader_aic=self.leader_aic,
            rabbitmq_config={
                "host": self.mq_config["host"],
                "port": self.mq_config["port"],
                "vhost": self.mq_config.get("vhost", "acps"),
                "auth_service_url": self.mq_config.get("auth_service_url"),
            },
            ssl_context=self.ssl_ctx,
            message_emitter=self.message_emitter,
            identity_binding_enabled=binding_enabled,
        )
        await self.leader._ensure_connection()
        self._heartbeat_task = asyncio.create_task(
            self.heartbeat_emitter.run_periodic(HEARTBEAT_INTERVAL)
        )
        logger.info("AcpsLeaderClient 已启动，AIC=%s", self.leader_aic)

    async def close(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
        if self.leader:
            try:
                await self.leader.close()
            except Exception as exc:
                logger.warning("关闭 GroupLeader 失败: %s", exc)

    # ------------------------------------------------------------------
    def _emit_access(self, callee_aic, method, url, route, status_code,
                     duration_ms, error_message=None, correlation_id=None):
        try:
            kwargs = {}
            if status_code >= 400:
                kwargs["error"] = ErrorInfo(
                    code=status_code, message=error_message or "",
                )
            body = AccessBody(
                duration_ms=duration_ms,
                request=AccessRequest(method=method, url=url, route=route),
                response=AccessResponse(status_code=status_code),
                caller=AccessParticipant(aic=self.leader_aic),
                callee=AccessParticipant(aic=callee_aic),
                **kwargs,
            )
            self.access_emitter.emit_sync(
                body=body, correlation_id=correlation_id or "",
            )
        except Exception as exc:
            logger.warning("access_emitter.emit_sync 失败: %s", exc)

    # ------------------------------------------------------------------
    @asynccontextmanager
    async def open_group(self, session_id: str, agent_aics: List[str]):
        agent_aics = [a for a in agent_aics if a]
        ctx = await self._open_group_impl(session_id, agent_aics)
        try:
            yield ctx
        finally:
            try:
                await asyncio.wait_for(
                    self.leader.dissolve_group_session(ctx.session.session_id),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("解散群组超时（10s），放弃等待")
            except Exception as exc:
                logger.warning("解散群组失败: %s", exc)
            finally:
                # ★ 记录解散时间，避免短时间内对同一 partner 重复邀请
                now = time.monotonic()
                for aic in ctx.agent_aics:
                    self._recent_dissolves[aic] = now

    # ------------------------------------------------------------------
    def _wait_recent_dissolve(self, agent_aics: List[str]):
        """
        若某 partner 最近 _DISSOLVE_COOLDOWN 秒内被解散过，等待至冷却结束。
        """
        now = time.monotonic()
        max_wait = 0.0
        for aic in agent_aics:
            last = self._recent_dissolves.get(aic)
            if last is None:
                continue
            elapsed = now - last
            if elapsed < _DISSOLVE_COOLDOWN:
                wait = _DISSOLVE_COOLDOWN - elapsed
                max_wait = max(max_wait, wait)
                logger.info(
                    "[_open_group_impl] %s 最近 %.1fs 内被解散，冷却中",
                    aic[-8:], elapsed,
                )
        if max_wait > 0:
            logger.info("[_open_group_impl] 等待冷却 %.1fs", max_wait)
            return max_wait
        return 0.0

    # ------------------------------------------------------------------
    async def _invite_one(self, aic: str, session_id: str,
                          session_leader_id: str) -> tuple[bool, Optional[str]]:
        """
        邀请单个 partner。
        返回 (success, error_kind)：
          - (True, None)                    邀请成功
          - (False, "pending")              已有 pending invitation
          - (False, "other:<msg>")          其他错误
        """
        t0 = time.monotonic()
        try:
            end_points = await self._get_partner_endpoints(aic)
            await self.leader.invite_partner(
                session_id=session_id,
                partner_acs=ACSObject(aic=aic),
                partner_acs_data={"endPoints": end_points},
            )
            logger.info("[_open_group_impl] 已邀请 %s", aic[-8:])
            self._emit_access(aic, "invite", "/invite", "/invite", 200,
                              (time.monotonic() - t0) * 1000,
                              correlation_id=session_leader_id)
            return True, None
        except PartnerInviteError as exc:
            msg = str(exc)
            if "already has a pending invitation" in msg:
                logger.warning(
                    "[_open_group_impl] %s 有旧的 pending 邀请", aic[-8:],
                )
                return False, "pending"
            logger.error("[_open_group_impl] 邀请 %s 失败: %s",
                         aic[-8:], exc, exc_info=True)
            self._emit_access(aic, "invite", "/invite", "/invite", 500,
                              (time.monotonic() - t0) * 1000,
                              error_message=msg,
                              correlation_id=session_leader_id)
            return False, f"other:{msg}"
        except Exception as exc:
            logger.error("[_open_group_impl] 邀请 %s 异常: %s",
                         aic[-8:], exc, exc_info=True)
            self._emit_access(aic, "invite", "/invite", "/invite", 500,
                              (time.monotonic() - t0) * 1000,
                              error_message=str(exc),
                              correlation_id=session_leader_id)
            return False, f"other:{exc}"

    # ------------------------------------------------------------------
    async def _open_group_impl(self, session_id: str,
                               agent_aics: List[str]) -> GroupCtx:
        logger.info("[_open_group_impl] 建群 session_id=%s, 邀请 %d 个 agent",
                    session_id, len(agent_aics))

        # ★ 先等冷却
        wait = self._wait_recent_dissolve(agent_aics)
        if wait > 0:
            await asyncio.sleep(wait)

        session = await self.leader.create_group_session(
            session_id=session_id,
            initial_partners=[ACSObject(aic=a) for a in agent_aics],
        )
        ctx = GroupCtx(session_id=session_id, session=session, agent_aics=agent_aics)

        # ---- 第一轮：并发邀请 ----
        pending: List[str] = []
        failed: List[str] = []

        for aic in agent_aics:
            ok, kind = await self._invite_one(
                aic, session.session_id, session.session_id,
            )
            if ok:
                continue
            if kind == "pending":
                pending.append(aic)
            else:
                failed.append(aic)

        # ---- 对 pending 的 partner 做退避重试 ----
        for attempt in range(_PENDING_RETRY_ATTEMPTS):
            if not pending:
                break
            delay = _PENDING_RETRY_BASE_DELAY * (attempt + 1)
            logger.info(
                "[_open_group_impl] pending 重试 #%d，等待 %.1fs，目标=%s",
                attempt + 1, delay, [a[-8:] for a in pending],
            )
            await asyncio.sleep(delay)

            still_pending: List[str] = []
            for aic in pending:
                ok, kind = await self._invite_one(
                    aic, session.session_id, session.session_id,
                )
                if ok:
                    logger.info("[_open_group_impl] 重试 #%d 成功邀请 %s",
                                attempt + 1, aic[-8:])
                elif kind == "pending":
                    still_pending.append(aic)
                else:
                    failed.append(aic)
            pending = still_pending

        # ---- 汇总未加入的 partner ----
        ctx.pending_partners = set(pending) | set(failed)
        if ctx.pending_partners:
            logger.warning(
                "[_open_group_impl] 以下 agent 未能加入: pending=%s, failed=%s",
                [a[-8:] for a in pending], [a[-8:] for a in failed],
            )

        # ---- 等待伙伴完成 join，然后核对真实成员 ----
        await asyncio.sleep(3)

        runtime = self.leader.get_group_runtime(session.session_id)
        members = {m["partner_aic"] for m in runtime.get("members", [])}
        logger.info(
            "[_open_group_impl] 群组真实成员数=%d, runtime_pending=%s",
            runtime.get("connected_members", 0),
            runtime.get("pending_invitations", []),
        )

        # 用 runtime 的真实成员再校正一次
        for aic in agent_aics:
            if aic not in members:
                ctx.pending_partners.add(aic)
                logger.warning("[_open_group_impl] ⚠️ agent %s 未在成员列表",
                               aic[-8:])

        return ctx

    # ------------------------------------------------------------------
    async def send_task_no_wait(self, ctx, agent_aic, image_path=None,
                                data_items=None, text_hint="") -> str:
        return await self._do_send_task(ctx, agent_aic, image_path,
                                        data_items, text_hint)

    async def wait_for_result(self, ctx, task_id, agent_aic,
                              timeout: int = 300) -> Dict[str, Any]:
        t0 = time.monotonic()
        try:
            result = await self._wait_for_result(ctx, task_id, agent_aic, timeout)
        except Exception as exc:
            self._emit_access(agent_aic, "start", "/start", "/start", 500,
                              (time.monotonic() - t0) * 1000,
                              error_message=str(exc), correlation_id=task_id)
            raise

        success = bool(result.get("success"))
        self._emit_access(
            agent_aic, "start", "/start", "/start",
            200 if success else 500,
            (time.monotonic() - t0) * 1000,
            error_message=(result.get("error") or "") if not success else None,
            correlation_id=task_id,
        )
        return result

    async def send_task(self, ctx, agent_aic, image_path=None,
                        data_items=None, text_hint="", timeout: int = 300):
        task_id = await self._do_send_task(ctx, agent_aic, image_path,
                                           data_items, text_hint)
        return await self.wait_for_result(ctx, task_id, agent_aic, timeout)

    # ------------------------------------------------------------------
    async def _do_send_task(self, ctx, agent_aic, image_path,
                            data_items, text_hint) -> str:
        # ★ 前置检查：不在群组里就别发
        if agent_aic in ctx.pending_partners:
            raise RuntimeError(f"agent {agent_aic[-8:]} 未加入群组，拒绝发送任务")

        logger.info("[_do_send_task] agent=%s image_path=%r exists=%s",
                    agent_aic[-8:], image_path,
                    os.path.isfile(image_path) if image_path else None)

        final_items: list = list(data_items) if data_items else []
        if not final_items and image_path:
            if not os.path.isfile(image_path):
                raise FileNotFoundError(f"本地文件不存在: {image_path}")
            final_items.append(FileDataItem(
                name=os.path.basename(image_path),
                mimeType=_guess_mime(image_path),
                uri=image_path,
                bytes=None,
            ))
        if text_hint:
            final_items.append(TextDataItem(text=text_hint))
        if not final_items:
            raise ValueError("缺少 data_items / image_path")

        task_id = f"task-{uuid.uuid4()}"
        await ctx.session.leader_mq_client.send_task_command(
            command=TaskCommandType.Start,
            task_id=task_id,
            session_id=ctx.session_id,
            text_content=None,
            data_items=final_items,
            mentions=[agent_aic],
        )
        logger.info("[_do_send_task] 已发任务 task_id=%s → %s",
                    task_id, agent_aic[-8:])
        return task_id

    async def _wait_for_result(self, ctx, task_id, agent_aic,
                               timeout: int) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        session = ctx.session
        last_seen = None
        delay = _WAIT_INITIAL

        while time.monotonic() < deadline:
            states = session.task_states.get(task_id, {}) or {}
            st = states.get(agent_aic)
            if st is not None and st != last_seen:
                logger.info("[wait] task=%s agent=%s state=%s",
                            task_id[-8:], agent_aic[-8:], st)
                last_seen = st

            if st is not None:
                if st == TaskState.Completed:
                    break
                if st in (TaskState.Failed, TaskState.Rejected, TaskState.Canceled):
                    err_text = self._collect_error(session, task_id, agent_aic)
                    logger.error("[wait] task=%s 失败 state=%s err=%r",
                                 task_id[-8:], st, err_text[:500])
                    return {
                        "success": False,
                        "error": f"任务失败: {st}, {err_text[:300]}",
                        "structured": None,
                    }
                if st == TaskState.AwaitingCompletion:
                    break

            await asyncio.sleep(delay)
            delay = min(delay * _WAIT_FACTOR, _WAIT_MAX)
        else:
            logger.error("[wait] 超时 task=%s timeout=%ss", task_id, timeout)
            return {"success": False, "error": f"等待结果超时（{timeout}s）"}

        return self._extract_result(session, task_id, agent_aic)

    def _collect_error(self, session, task_id, agent_aic) -> str:
        err_text = ""
        for msg in reversed(session.message_history):
            if getattr(msg, "taskId", None) != task_id:
                continue
            if getattr(msg, "senderId", None) != agent_aic:
                continue
            if type(msg).__name__ != "TaskResult":
                continue
            for item in (getattr(getattr(msg, "status", None), "dataItems", None) or []):
                if getattr(item, "type", None) == "text":
                    err_text += (getattr(item, "text", "") or "") + "\n"
            break
        return err_text

    def _extract_result(self, session, task_id, agent_aic) -> Dict[str, Any]:
        text = ""
        file_items: List[Any] = []
        seen_keys: set = set()

        def _file_key(item):
            return (
                getattr(item, "name", None) or "",
                getattr(item, "uri", None) or "",
                len(getattr(item, "bytes", None) or ""),
            )

        for msg in reversed(session.message_history):
            if getattr(msg, "taskId", None) != task_id:
                continue
            if getattr(msg, "senderId", None) != agent_aic:
                continue
            if type(msg).__name__ != "TaskResult":
                continue

            for product in (getattr(msg, "products", None) or []):
                for item in (getattr(product, "dataItems", None) or []):
                    t = getattr(item, "type", None)
                    if t == "text" and not text:
                        text = getattr(item, "text", "") or ""
                    elif t == "file":
                        k = _file_key(item)
                        if k not in seen_keys:
                            seen_keys.add(k)
                            file_items.append(item)

            for item in (getattr(getattr(msg, "status", None), "dataItems", None) or []):
                if getattr(item, "type", None) == "file":
                    k = _file_key(item)
                    if k not in seen_keys:
                        seen_keys.add(k)
                        file_items.append(item)

            if text or file_items:
                break

        structured = None
        try:
            structured = json.loads(text)
        except Exception:
            pass

        logger.info("[wait] task=%s agent=%s text[:500]=%r file_items=%d",
                    task_id[-8:], agent_aic[-8:], text[:500], len(file_items))
        return {
            "success": True,
            "text": text,
            "structured": structured,
            "file_items": file_items,
        }

    async def _get_partner_endpoints(self, agent_aic: str) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                DISCOVERY_URL,
                json={
                    "type": "filtered",
                    "query": "",
                    "limit": 5,
                    "filter": {
                        "conditions": [{"field": "aic", "op": "eq", "value": agent_aic}]
                    },
                },
            )
            data = resp.json()
            acs_map = (data.get("result") or {}).get("acsMap") or {}
            acs = acs_map.get(agent_aic)
            if not acs:
                raise Exception(f"Discovery 里没找到 {agent_aic}")
            eps = acs.get("endPoints", []) or []
            amqp_eps = [e for e in eps if (e.get("transport") or "").upper() == "AMQP"]
            return amqp_eps or [{
                "url": f"amqps://wt.ioa.pub:5671/acps?inbox=inbox_{agent_aic}",
                "transport": "AMQP",
            }]