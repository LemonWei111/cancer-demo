# common/acps_subagent_adapter.py
"""
ACPs 群组模式通用适配层（Partner 侧）。

- 从 acps_config 读配置
- 从 acps_utils 复用工具
- 从 acps_emitters 复用 emitter + sampler
- 群组 client 收到 DISBAND 后延迟关闭，避免泄漏
- 并发计数用 inc_active 而非直接设值
"""
import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from acps_sdk.aip import (
    TaskCommand, TaskResult, TaskState, TextDataItem, FileDataItem, Product,
)
from acps_sdk.aip.aip_group_partner import (
    GroupPartnerMqClient, extract_text_from_command,
)
from acps_sdk.aip.aip_group_model import InboxGroupInvitation, GroupMgmtCommandType
from acps_sdk.amp import (
    AccessBody, AccessRequest, AccessResponse, AccessParticipant, ErrorInfo,
)

from acps_config import (
    build_mq_ssl_context, AMP_LOG_DIR, is_identity_binding_enabled,
    HEARTBEAT_INTERVAL, METRICS_INTERVAL,
)
from acps_emitters import (
    KafkaHeartbeatEmitter, KafkaMetricsEmitter,
    KafkaAccessEmitter, KafkaMessageEmitter,
    get_forwarder, WsiMetricsSampler,
)
from acps_utils import (
    b64_to_local_file, file_item_to_path, pack_files_as_dataitems,
)

logger = logging.getLogger("AcpsSubAgentAdapter")


# ---------------------------------------------------------------------------
# 输入提取
# ---------------------------------------------------------------------------
def _extract_input_image(command: TaskCommand) -> Optional[str]:
    for item in command.dataItems or []:
        if getattr(item, "type", None) != "file":
            continue
        p = file_item_to_path(item, prefer_uri=False)
        if p:
            logger.info("[_extract_input_image] 从 FileDataItem 取到 %r", p)
            return p

    text = extract_text_from_command(command) or ""
    logger.debug("[_extract_input_image] text=%r", text[:300])
    match = re.search(
        r"(/[\S]+\.(?:jpg|jpeg|png|tif|tiff|svs|ndpi|mrxs|wsi))",
        text, re.IGNORECASE,
    )
    if match:
        p = match.group(1)
        if os.path.isfile(p):
            return p
    return None


# ---------------------------------------------------------------------------
# 默认结果适配器
# ---------------------------------------------------------------------------
def default_result_adapter(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict):
        text = json.dumps(result, ensure_ascii=False)
    elif isinstance(result, (list, tuple)):
        text = json.dumps(list(result), ensure_ascii=False)
    else:
        text = str(result)
    return {"text": text, "files": []}


InputResolver = Callable[[TaskCommand, "AcpsSubAgentPartner"], Awaitable[Optional[str]]]


# ---------------------------------------------------------------------------
# 通用 Partner
# ---------------------------------------------------------------------------
class AcpsSubAgentPartner:
    def __init__(
        self,
        agent_name: str,
        partner_aic: str,
        mq_config: dict,
        analyze_func: Callable[[str], Any],
        result_adapter: Optional[Callable[[Any], Dict[str, Any]]] = None,
        input_resolver: Optional[InputResolver] = None,
        identity_binding_enabled: Optional[bool] = None,   # ← 改为 Optional
        amp_log_dir: Optional[str] = None,
    ):
        self.agent_name = agent_name
        self.partner_aic = partner_aic
        self.analyze_func = analyze_func
        self.result_adapter = result_adapter or default_result_adapter
        self.input_resolver = input_resolver

        # 未显式传 → 用全局配置
        self.identity_binding_enabled = (
            identity_binding_enabled
            if identity_binding_enabled is not None
            else is_identity_binding_enabled()
        )

        self._mq_config = mq_config
        self._group_clients: Dict[str, GroupPartnerMqClient] = {}
        self._peer_files: Dict[str, List[FileDataItem]] = {}

        self._ssl_ctx = build_mq_ssl_context()

        self.metrics_sampler = WsiMetricsSampler()
        amp_dir = Path(amp_log_dir or str(AMP_LOG_DIR))
        amp_dir.mkdir(parents=True, exist_ok=True)
        get_forwarder()

        self.heartbeat_emitter = KafkaHeartbeatEmitter(
            log_file=amp_dir / f"amp_heartbeat_{partner_aic}.jsonl", aic=partner_aic,
        )
        self.metrics_emitter = KafkaMetricsEmitter(
            log_file=amp_dir / f"amp_metrics_{partner_aic}.jsonl", aic=partner_aic,
            sampler=self.metrics_sampler,
        )
        self.access_emitter = KafkaAccessEmitter(
            log_file=amp_dir / f"amp_access_{partner_aic}.jsonl", aic=partner_aic,
        )
        self.message_emitter = KafkaMessageEmitter(
            log_file=amp_dir / f"amp_message_{partner_aic}.jsonl", aic=partner_aic,
        )

        self.inbox_client = GroupPartnerMqClient(
            partner_aic=partner_aic,
            rabbitmq_host=mq_config["host"],
            rabbitmq_port=mq_config["port"],
            rabbitmq_vhost=mq_config.get("vhost", "acps"),
            rabbitmq_user=None,
            rabbitmq_password=None,
            ssl_context=self._ssl_ctx,
            identity_binding_enabled=self.identity_binding_enabled,
        )

        self._heartbeat_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    def get_peer_files(self, sender_aic: str) -> List[FileDataItem]:
        return self._peer_files.get(sender_aic, [])

    def get_leader_files(self, leader_aic: str) -> List[FileDataItem]:
        return self._peer_files.get(leader_aic, [])

    # ------------------------------------------------------------------
    def _build_group_client(self) -> GroupPartnerMqClient:
        client = GroupPartnerMqClient(
            partner_aic=self.partner_aic,
            rabbitmq_host=self._mq_config["host"],
            rabbitmq_port=self._mq_config["port"],
            rabbitmq_vhost=self._mq_config.get("vhost", "acps"),
            rabbitmq_user=None,
            rabbitmq_password=None,
            ssl_context=self._ssl_ctx,
            message_emitter=self.message_emitter,
            identity_binding_enabled=self.identity_binding_enabled,
        )

        async def on_peer_task_result(task_result: TaskResult):
            try:
                cached = False
                for product in (task_result.products or []):
                    for item in (product.dataItems or []):
                        if getattr(item, "type", None) == "file":
                            self._peer_files.setdefault(
                                task_result.senderId, []
                            ).append(item)
                            cached = True
                if cached:
                    logger.debug(
                        "缓存邻居 %s 的 FileDataItem: 当前 %d 个",
                        task_result.senderId[-8:],
                        len(self._peer_files.get(task_result.senderId, [])),
                    )
            except Exception as exc:
                logger.warning("缓存邻居文件失败: %s", exc)

        client.set_task_result_handler(on_peer_task_result)
        return client
    
    # ------------------------------------------------------------------
    async def start(self):
        logger.info("[start] %s 启动，AIC=%s pid=%s",
                    self.agent_name, self.partner_aic, os.getpid())

        await self.inbox_client.connect()
        inbox_queue = await self.inbox_client.ensure_inbox_queue()
        logger.info("[start] inbox_queue.name=%s", getattr(inbox_queue, "name", None))

        try:
            await inbox_queue.purge()
            logger.info("[start] inbox 队列已清空")
        except Exception as exc:
            logger.warning("[start] 清空 inbox 失败: %s", exc)

        await self.inbox_client.start_inbox_consuming(self._on_inbox)

        self._heartbeat_task = asyncio.create_task(
            self.heartbeat_emitter.run_periodic(HEARTBEAT_INTERVAL)
        )
        self._metrics_task = asyncio.create_task(
            self.metrics_emitter.run_periodic(METRICS_INTERVAL)
        )
        logger.info("[start] %s 已启动", self.agent_name)

    async def close(self):
        tasks = [t for t in (self._heartbeat_task, self._metrics_task) if t]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for gid, gc in list(self._group_clients.items()):
            try:
                await gc.close()
            except Exception as exc:
                logger.warning("关闭群组 %s 失败: %s", gid, exc)
        self._group_clients.clear()
        await self.inbox_client.close()

    # ------------------------------------------------------------------
    async def _on_inbox(self, invitation: InboxGroupInvitation):
        group_id = invitation.group.groupId
        if group_id in self._group_clients:
            logger.warning("群组 %s 已存在 client，忽略重复邀请", group_id)
            return

        group_client = self._build_group_client()

        async def on_command(command: TaskCommand, is_mentioned: bool):
            if not is_mentioned:
                return
            await self._handle_command(command, group_client)

        group_client.set_command_handler(on_command)

        # ---- 关键改动：DISBAND 只清引用，不主动 close ----
        async def on_mgmt_command(command):
            if command.command == GroupMgmtCommandType.DISBAND:
                gid = command.groupId
                gc = self._group_clients.pop(gid, None)
                if gc is not None:
                    logger.info(
                        "DISBAND 收到，本地引用已清理 group=%s（SDK 负责收尾）", gid,
                    )

        group_client.set_mgmt_command_handler(on_mgmt_command)

        try:
            await group_client.join_group_from_invitation(invitation)
            self._group_clients[group_id] = group_client
            logger.info("群组 %s 加入成功，活跃数=%d", group_id, len(self._group_clients))
        except Exception as exc:
            logger.error("加入群组 %s 失败: %s", group_id, exc, exc_info=True)
            try:
                await group_client.close()
            except Exception:
                pass

    async def _close_group_client_delayed(self, gc, gid):
        await asyncio.sleep(1)
        try:
            await gc.close()
            logger.info("群组 %s 的 client 已关闭", gid)
        except Exception as exc:
            logger.warning("关闭群组 %s 失败: %s", gid, exc)

    # ------------------------------------------------------------------
    async def _handle_command(
        self, command: TaskCommand, group_client: GroupPartnerMqClient
    ):
        session_id = command.sessionId or "unknown"
        task_id = command.taskId
        start_ts = time.monotonic()
        self.metrics_sampler.inc_active(+1)

        try:
            if command.command != "start":
                return

            logger.info("[_handle_command] task_id=%s session_id=%s",
                        task_id, session_id)

            await group_client.accept_task(task_id, session_id)
            try:
                await group_client.start_working(task_id, session_id)
            except Exception:
                await group_client.fail_task(task_id, session_id, "start_working 失败")
                raise

            # 1. command 里提取
            local_input_path = _extract_input_image(command)

            # 2. input_resolver
            if not local_input_path and self.input_resolver:
                try:
                    local_input_path = await self.input_resolver(command, self)
                except Exception as exc:
                    logger.warning("[_handle_command] input_resolver 失败: %s", exc)

            if not local_input_path:
                await group_client.fail_task(task_id, session_id, "缺少可用的图片输入")
                return

            # 3. 业务
            result = await asyncio.to_thread(self.analyze_func, local_input_path)

            # 4. 结果适配
            adapted = self.result_adapter(result)
            text = adapted.get("text", "")
            files = adapted.get("files", []) or []

            file_items = pack_files_as_dataitems(files)

            products = [Product(
                id=f"result-{task_id}",
                name=f"{self.agent_name} 结果",
                description=text[:200],
                dataItems=[TextDataItem(text=text)] + file_items,
            )]

            await group_client.send_task_result(
                task_id=task_id,
                session_id=session_id,
                state=TaskState.Completed,
                products=products,
                status_data_items=[TextDataItem(text=f"{self.agent_name} 完成")],
            )

            self._emit_access(command.senderId, task_id, 200, start_ts)

        except asyncio.CancelledError:
            logger.warning("[_handle_command] 任务被取消 task_id=%s", task_id)
            raise
        except Exception as exc:
            logger.exception("[_handle_command] 处理失败: %s", exc)
            try:
                await group_client.fail_task(task_id, session_id, str(exc))
            except Exception:
                pass
            self._emit_access(command.senderId, task_id, 500, start_ts, str(exc))
        finally:
            self.metrics_sampler.inc_active(-1)

    def _emit_access(
        self, caller_aic: str, task_id: str, status_code: int,
        start_ts: float, err: Optional[str] = None,
    ):
        elapsed_ms = (time.monotonic() - start_ts) * 1000
        try:
            body = AccessBody(
                duration_ms=elapsed_ms,
                request=AccessRequest(
                    method="start", url=f"inbox_{self.partner_aic}", route="inbox",
                ),
                response=AccessResponse(status_code=status_code),
                caller=AccessParticipant(aic=caller_aic),
                callee=AccessParticipant(aic=self.partner_aic),
                **({"error": ErrorInfo(code=status_code, message=err or "")}
                   if status_code >= 400 else {}),
            )
            self.access_emitter.emit_sync(body=body, correlation_id=task_id)
        except Exception as exc:
            logger.warning("access 上报失败: %s", exc)