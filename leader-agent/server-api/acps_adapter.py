# leader-agent/server-api/acps_adapter.py
"""
ACPs 群组模式适配层：把 WsiLeaderService 包装成群组 Partner。

本次改动：
- `_ensure_in_seg_dir`：把 seg/其他 agent 输出的文件"发布"到 SEG_RESULT_DIR
- `_to_public_url`：不再丢失子目录（brca-contour/ 等）
- `_build_products`：加 URL 日志，便于排查
- DISBAND 只清引用，不主动 close（避免 ChannelInvalidStateError）
- metrics 计数用 inc_active 并发安全
- identity_binding_enabled 用惰性函数读取
"""
import asyncio
import base64
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel
from PIL import Image
import io

from acps_sdk.aip import (
    TaskCommand, TaskResult, TaskState, TextDataItem, FileDataItem, Product,
)
from acps_sdk.aip.aip_group_partner import (
    GroupPartnerMqClient, extract_text_from_command,
)
from acps_sdk.aip.aip_group_model import InboxGroupInvitation, GroupMgmtCommandType
from acps_sdk.aip.aip_rpc_server import (
    CommandHandlers, TaskManager, add_aip_rpc_router,
)
from acps_sdk.amp import (
    AccessBody, AccessRequest, AccessResponse, AccessParticipant, ErrorInfo,
)

from acps_config import (
    build_mq_ssl_context, AMP_LOG_DIR, UPLOAD_DIR, SEG_RESULT_DIR,
    LEADER_PUBLIC_BASE_URL, HEARTBEAT_INTERVAL, METRICS_INTERVAL,
    is_identity_binding_enabled,
)
from acps_emitters import (
    KafkaHeartbeatEmitter, KafkaMetricsEmitter,
    KafkaAccessEmitter, KafkaMessageEmitter,
    get_forwarder, WsiMetricsSampler,
)
from acps_utils import force_ext as _force_ext_util

logger = logging.getLogger("AcpsAdapter")


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------
class CoordinationPayload(BaseModel):
    message: str
    session_id: Optional[str] = None
    wsi_path: Optional[str] = None


# ---------------------------------------------------------------------------
# 输入提取
# ---------------------------------------------------------------------------
def _save_base64_image(data: str, mime: str = "image/png") -> Optional[str]:
    try:
        if "," in data:
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
        ext = ".png"
        if "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif "tiff" in mime:
            ext = ".tiff"
        filename = f"{uuid.uuid4().hex}{ext}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        save_path = os.path.join(UPLOAD_DIR, filename)
        with open(save_path, "wb") as fp:
            fp.write(raw)
        logger.info("[_save_base64_image] 落盘 path=%s size=%d", save_path, len(raw))
        return save_path
    except Exception as exc:
        logger.error("[_save_base64_image] 失败: %s", exc, exc_info=True)
        return None


def _extract_inputs(command: TaskCommand):
    """从 TaskCommand.dataItems 提取 (user_input, wsi_path)。"""
    user_input = ""
    wsi_path = None

    for item in command.dataItems or []:
        item_type = getattr(item, "type", None)

        if item_type == "text":
            text = getattr(item, "text", "") or ""
            if text.strip():
                user_input = text.strip()
                match = re.search(
                    r"(/[\S]+\.(?:wsi|svs|tif|tiff|ndpi|mrxs|jpg|jpeg|png))",
                    text, re.IGNORECASE,
                )
                if match:
                    wsi_path = match.group(1)
            continue

        if item_type == "file":
            uri = getattr(item, "uri", None)
            raw_bytes = getattr(item, "bytes", None)
            mime = getattr(item, "mimeType", None) or "image/png"

            if uri and (os.path.isabs(uri) or uri.startswith("/")):
                wsi_path = uri
            elif raw_bytes:
                saved = _save_base64_image(raw_bytes, mime)
                if saved:
                    wsi_path = saved
            continue

    params = command.commandParams or {}
    if params.get("wsiPath"):
        wsi_path = params["wsiPath"]
    elif params.get("wsi_path"):
        wsi_path = params["wsi_path"]

    return user_input, wsi_path


# ---------------------------------------------------------------------------
# 文件发布到 SEG_RESULT_DIR
# ---------------------------------------------------------------------------
def _ensure_in_seg_dir(local_path: str) -> str:
    """
    确保文件在 SEG_RESULT_DIR 下，返回可在 URL 中引用的路径。

    - 已在 SEG_RESULT_DIR 下 → 返回原路径
    - 不在 → 复制到 SEG_RESULT_DIR/<basename>，返回新路径
    - 文件不存在 → 原样返回（调用方处理）

    注意：
    - 这里**不保留原相对目录**（basename 落地）。如果未来需要保留
      子目录（例如按 session 分子目录），可以改成按 `relpath` 复制。
    """
    if not local_path or not os.path.isfile(local_path):
        return local_path

    seg_dir = str(SEG_RESULT_DIR)
    real = os.path.realpath(local_path)

    # 已在下 → 直接返回
    if real.startswith(seg_dir + os.sep) or os.path.dirname(real) == seg_dir:
        return real

    os.makedirs(seg_dir, exist_ok=True)
    target = os.path.join(seg_dir, os.path.basename(local_path))
    if not os.path.exists(target):
        try:
            shutil.copy2(local_path, target)
            logger.info(
                "[_ensure_in_seg_dir] 发布文件: %s → %s", local_path, target,
            )
        except Exception as exc:
            logger.warning(
                "[_ensure_in_seg_dir] 复制失败 %s → %s: %s",
                local_path, target, exc,
            )
            return local_path
    return target


def _to_public_url(local_path: str) -> str:
    """
    拼 /wsi-result/<rel> 的 URL。
    要求 local_path 已在 SEG_RESULT_DIR 下；否则退回 basename。
    """
    if not local_path:
        return ""
    seg_dir = str(SEG_RESULT_DIR)
    rel = os.path.relpath(local_path, seg_dir)
    if rel.startswith(".."):
        # 兜底：理论上 _ensure_in_seg_dir 已经把文件搬进来了
        logger.warning(
            "[_to_public_url] %s 不在 SEG_RESULT_DIR 下，退化为 basename",
            local_path,
        )
        rel = os.path.basename(local_path)
    rel = rel.replace(os.sep, "/")
    return f"{LEADER_PUBLIC_BASE_URL}/wsi-result/{rel}"


def _fix_name_suffix(name: str, mime: str) -> str:
    """让 name 的后缀跟 mime 一致（复用 acps_utils.force_ext）。"""
    return _force_ext_util(name, mime)


def _compress_image(image_path: str, max_size: int = 800, quality: int = 85) -> bytes:
    img = Image.open(image_path)
    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _build_products(command: TaskCommand, result: dict) -> list:
    """
    把协调结果包装成 ACPs Product 列表。

    关键流程：
    1. seg 输出的 FileDataItem 里 uri 是本地绝对路径（可能在
       partners/wsi-result/...，也可能在 wsi-result/...）
    2. 先用 _ensure_in_seg_dir 把文件"发布"到 SEG_RESULT_DIR
    3. 再用 _to_public_url 拼 BASE_URL + /wsi-result/<rel>
    4. name 后缀与 mime 一致
    """
    final_text = result.get("final_response", "")
    data_items = [TextDataItem(text=final_text)]

    agent_results = result.get("agent_results") or {}
    seg_result = agent_results.get("seg-agent-001") or {}
    file_items = seg_result.get("file_items") or []

    logger.info(
        "[_build_products] seg file_items 数=%d, names=%s",
        len(file_items),
        [getattr(it, "name", None) for it in file_items],
    )

    # contour 优先，transparent fallback
    contour_item = None
    transparent_item = None
    other_items = []
    for item in file_items:
        if getattr(item, "type", None) != "file":
            continue
        name = (getattr(item, "name", "") or "").lower()
        if "contour" in name and contour_item is None:
            contour_item = item
        elif "transparent" in name and transparent_item is None:
            transparent_item = item
        else:
            other_items.append(item)

    ordered = []
    if contour_item is not None:
        ordered.append(contour_item)
    elif transparent_item is not None:
        ordered.append(transparent_item)
    ordered.extend(other_items)

    for item in ordered:
        raw_uri = getattr(item, "uri", "") or ""
        mime = getattr(item, "mimeType", None) or "image/png"
        raw_name = getattr(item, "name", None) or "result"

        # ★ 1. 发布到 SEG_RESULT_DIR
        published_path = _ensure_in_seg_dir(raw_uri)

        # ★ 2. 拼 URL（保留子目录）
        url = _to_public_url(published_path)

        # ★ 3. 后缀与 mime 一致
        fixed_name = _fix_name_suffix(raw_name, mime)

        new_item = FileDataItem(
            name=fixed_name,
            mimeType=mime,
            uri=url,
            bytes=getattr(item, "bytes", None),
        )
        logger.info(
            "[_build_products] FileDataItem: name=%s, mimeType=%s, uri=%s, bytes_len=%d",
            new_item.name,
            new_item.mimeType,
            new_item.uri,
            len(new_item.bytes) if new_item.bytes else 0,
        )
        data_items.append(new_item)

    logger.info(
        "[_build_products] 最终 dataItems 数=%d（含 FileDataItem=%d）",
        len(data_items),
        sum(1 for x in data_items if getattr(x, "type", None) == "file"),
    )

    return [Product(
        id=f"report-{command.taskId}",
        name="病理分析报告",
        description="包含文本诊断报告与分割结果图像",
        dataItems=data_items,
    )]


# ---------------------------------------------------------------------------
# 群组 Partner
# ---------------------------------------------------------------------------
class AcpsGroupPartner:
    def __init__(self, service, partner_aic: str, mq_config: dict,
                 identity_binding_enabled: Optional[bool] = None):
        self.service = service
        self.partner_aic = partner_aic
        self._heartbeat_task = None
        self._metrics_task = None

        self.metrics_sampler = WsiMetricsSampler()
        self._group_clients: dict = {}
        self._mq_config = mq_config

        # 未显式传 → 用全局配置（惰性函数，避免 import 时机问题）
        self.identity_binding_enabled = (
            identity_binding_enabled
            if identity_binding_enabled is not None
            else is_identity_binding_enabled()
        )

        amp_dir = Path(str(AMP_LOG_DIR))
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

        self._ssl_ctx = build_mq_ssl_context()

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

        self._setup_handlers()

    # ------------------------------------------------------------------
    def _build_group_client(self) -> GroupPartnerMqClient:
        return GroupPartnerMqClient(
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

    def _setup_handlers(self):
        async def on_invitation(invitation: InboxGroupInvitation) -> bool:
            logger.info("收到群组邀请 group_id=%s leader=%s",
                        invitation.group.groupId, invitation.group.leader.aic)
            return True

        self.inbox_client.set_invitation_handler(on_invitation)

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

        # ★ DISBAND 只清引用，不主动 close（避免 ChannelInvalidStateError）
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

    # ------------------------------------------------------------------
    async def _handle_command(self, command: TaskCommand,
                              group_client: GroupPartnerMqClient):
        session_id = command.sessionId or "unknown"
        task_id = command.taskId
        start_ts = time.monotonic()
        self.metrics_sampler.inc_active(+1)   # ★ 并发安全

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

            user_input, wsi_path = _extract_inputs(command)
            if not user_input and not wsi_path:
                await group_client.fail_task(task_id, session_id, "缺少用户输入和图片")
                return

            payload = CoordinationPayload(
                message=user_input or "请分析这张切片",
                session_id=session_id,
                wsi_path=wsi_path,
            )
            result = await self.service.handle_coordination(payload, session_id)
            products = _build_products(command, result)

            await group_client.send_task_result(
                task_id=task_id,
                session_id=session_id,
                state=TaskState.AwaitingCompletion,
                products=products,
                status_data_items=[TextDataItem(text="分析完成，等待确认")],
            )

            final_text = result.get("final_response", "")
            status_data_items = [TextDataItem(text=final_text)]
            for product in products:
                for item in product.dataItems:
                    if item.type == "file":
                        status_data_items.append(item)

            await group_client.send_task_result(
                task_id=task_id,
                session_id=session_id,
                state=TaskState.Completed,
                products=products,
                status_data_items=status_data_items,
            )

            self._emit_access(command.senderId, task_id, 200, start_ts)

        except Exception as exc:
            logger.exception("处理群组命令失败: %s", exc)
            try:
                await group_client.fail_task(task_id, session_id, f"处理失败: {exc}")
            except Exception:
                pass
            self._emit_access(command.senderId, task_id, 500, start_ts, str(exc))
        finally:
            self.metrics_sampler.inc_active(-1)   # ★ 并发安全

    def _emit_access(self, caller_aic: str, task_id: str, status_code: int,
                     start_ts: float, err: Optional[str] = None):
        elapsed_ms = (time.monotonic() - start_ts) * 1000
        try:
            kwargs = {}
            if status_code >= 400:
                kwargs["error"] = ErrorInfo(code=status_code, message=err or "")
            body = AccessBody(
                duration_ms=elapsed_ms,
                request=AccessRequest(
                    method="start", url=f"inbox_{self.partner_aic}", route="inbox",
                ),
                response=AccessResponse(status_code=status_code),
                caller=AccessParticipant(aic=caller_aic),
                callee=AccessParticipant(aic=self.partner_aic),
                **kwargs,
            )
            self.access_emitter.emit_sync(body=body, correlation_id=task_id)
        except Exception as exc:
            logger.warning("access 上报失败: %s", exc)

    async def start(self):
        await self.inbox_client.connect()
        inbox_queue = await self.inbox_client.ensure_inbox_queue()
        try:
            await inbox_queue.purge()
            logger.info("inbox 队列已清空")
        except Exception as exc:
            logger.warning("清空 inbox 失败: %s", exc)

        await self.inbox_client.start_inbox_consuming(self._on_inbox)
        logger.info("ACPs 群组 Partner 已启动，AIC=%s", self.partner_aic)

        self._heartbeat_task = asyncio.create_task(
            self.heartbeat_emitter.run_periodic(HEARTBEAT_INTERVAL)
        )
        self._metrics_task = asyncio.create_task(
            self.metrics_emitter.run_periodic(METRICS_INTERVAL)
        )

    async def close(self):
        tasks = [t for t in (self._heartbeat_task, self._metrics_task) if t]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for group_id, group_client in list(self._group_clients.items()):
            try:
                await group_client.close()
            except Exception as exc:
                logger.warning("关闭群组 %s 失败: %s", group_id, exc)
        self._group_clients.clear()
        await self.inbox_client.close()


async def start_acps_group_partner(service, partner_aic: str,
                                   mq_config: dict) -> AcpsGroupPartner:
    partner = AcpsGroupPartner(service, partner_aic, mq_config)
    await partner.start()
    return partner


# ---------------------------------------------------------------------------
# 直连模式（HTTP RPC）
# ---------------------------------------------------------------------------
def build_acps_handlers(service) -> CommandHandlers:
    async def on_start(command: TaskCommand, task: Optional[TaskResult]) -> TaskResult:
        if task is not None:
            TaskManager.add_command_to_history(command.taskId, command)
            return task

        task = TaskManager.create_task(command)

        try:
            user_input, wsi_path = _extract_inputs(command)
            session_id = command.sessionId or f"acps-{command.taskId}"

            if not user_input and not wsi_path:
                TaskManager.update_task_status(
                    command.taskId, TaskState.Failed,
                    data_items=[TextDataItem(text="缺少用户输入（dataItems 为空）")],
                )
                return TaskManager.get_task(command.taskId)

            TaskManager.update_task_status(command.taskId, TaskState.Working)

            payload = CoordinationPayload(
                message=user_input or "请分析这张切片",
                session_id=session_id,
                wsi_path=wsi_path,
            )
            result = await service.handle_coordination(payload, session_id)
            products = _build_products(command, result)
            TaskManager.set_products(command.taskId, products)

            final_text = result.get("final_response", "")
            TaskManager.update_task_status(
                command.taskId, TaskState.Completed,
                data_items=[TextDataItem(text=final_text)],
            )
        except Exception as exc:
            logger.exception("ACPs on_start 处理失败: %s", exc)
            TaskManager.update_task_status(
                command.taskId, TaskState.Failed,
                data_items=[TextDataItem(text=f"处理失败: {exc}")],
            )

        return TaskManager.get_task(command.taskId)

    async def on_get(command: TaskCommand, task: TaskResult) -> TaskResult:
        TaskManager.add_command_to_history(command.taskId, command)
        return task

    async def on_cancel(command: TaskCommand, task: TaskResult) -> TaskResult:
        terminal = {TaskState.Completed, TaskState.Failed,
                    TaskState.Rejected, TaskState.Canceled}
        if task.status.state in terminal:
            return task
        TaskManager.add_command_to_history(command.taskId, command)
        return TaskManager.update_task_status(command.taskId, TaskState.Canceled)

    async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
        TaskManager.add_command_to_history(command.taskId, command)
        return task

    async def on_complete(command: TaskCommand, task: TaskResult) -> TaskResult:
        TaskManager.add_command_to_history(command.taskId, command)
        if task.status.state == TaskState.AwaitingCompletion:
            return TaskManager.update_task_status(command.taskId, TaskState.Completed)
        return task

    return CommandHandlers(
        on_start=on_start, on_get=on_get, on_cancel=on_cancel,
        on_continue=on_continue, on_complete=on_complete,
    )


def add_acps_routes(app: FastAPI, service, *, local_aic: Optional[str] = None,
                    endpoint: str = "/aip/rpc",
                    identity_binding_enabled: bool = False):
    handlers = build_acps_handlers(service)
    add_aip_rpc_router(app, endpoint=endpoint, agent_handlers=handlers)

    @app.get("/aip/ping")
    async def _ping():
        return {"pong": True}

    logger.info("ACPs AIP RPC 路由已挂载: %s (local_aic=%s)", endpoint, local_aic)