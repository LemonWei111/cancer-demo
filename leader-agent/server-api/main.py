#!/usr/bin/env python3
# leader-agent/server-api/main.py
"""病理切片多智能体协调分析 Leader。"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import openai
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from acps_sdk.aip.aip_group_leader import GroupLeader
from acps_sdk.aip.aip_group_runtime import (
    build_inbox_queue_name, INBOX_EXCHANGE_NAME,
    INBOX_QUEUE_EXPIRES_MS, INBOX_MESSAGE_TTL_MS,
)
from aio_pika import ExchangeType

from acps_config import (
    PROJECT_ROOT as _PROJECT_ROOT_CFG,
    UPLOAD_DIR as _UPLOAD_DIR_CFG,
    SEG_RESULT_DIR as _SEG_RESULT_DIR_CFG,
    LEADER_AGENT_PORT as HTTP_PORT,
    TIMEOUT_PREDICTOR, TIMEOUT_SEG, TIMEOUT_ALNPRE,
    mq_config as _mq_config_fn,
)


# ===========================================================================
# LeaderGroupLeader：只声明 inbox 队列不 consume，避免抢消息
# ===========================================================================
class LeaderGroupLeader(GroupLeader):
    """Leader 侧 GroupLeader：inbox 只声明绑定，不消费。"""

    async def _ensure_inbox(self):
        if self._inbox_queue and not getattr(self._inbox_queue, "is_closed", False):
            return
        if not self._connection:
            raise RuntimeError("RabbitMQ connection is not ready")
        inbox_name = build_inbox_queue_name(self.leader_aic)
        self._inbox_channel = await self._connection.channel()
        self._inbox_queue = await self._inbox_channel.declare_queue(
            inbox_name, durable=True, exclusive=False, auto_delete=False,
            arguments={
                "x-expires": INBOX_QUEUE_EXPIRES_MS,
                "x-message-ttl": INBOX_MESSAGE_TTL_MS,
            },
        )
        inbox_exchange = await self._inbox_channel.declare_exchange(
            name=INBOX_EXCHANGE_NAME, type=ExchangeType.TOPIC,
            durable=True, auto_delete=False,
        )
        await self._inbox_queue.bind(inbox_exchange, routing_key=inbox_name)
        # 不 consume


# 在 import AcpsLeaderClient 之前生效
import acps_leader_client
acps_leader_client.GroupLeader = LeaderGroupLeader
# ===========================================================================

from acps_adapter import start_acps_group_partner, add_acps_routes, CoordinationPayload
from acps_leader_client import AcpsLeaderClient

load_dotenv()

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
_PROJECT_ROOT = str(_PROJECT_ROOT_CFG)
UPLOAD_DIR = str(_UPLOAD_DIR_CFG)
SEG_RESULT_DIR = str(_SEG_RESULT_DIR_CFG)
LEADER_AGENT_ID = os.getenv("LEADER_AGENT_ID", "wsi-analyzer-agent-001")
ACPS_AIC = os.getenv("ACPS_AIC", "")
CLIENT_WEB_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "client-web")
)

AGENT_CONFIG: Dict[str, Dict[str, str]] = {
    "brca-pre-agent-001": {
        "name": "乳腺组织切片预测智能体",
        "capability": "乳腺癌切片预测",
        "url": f"http://localhost:{os.getenv('BRCA_PRE_AGENT_PORT', '8011')}/analyze",
        "aic": os.getenv("BRCA_PRE_AIC", ""),
    },
    "coad-pre-agent-001": {
        "name": "结肠组织切片预测智能体",
        "capability": "结肠切片预测",
        "url": f"http://localhost:{os.getenv('COAD_PRE_AGENT_PORT', '8012')}/analyze",
        "aic": os.getenv("COAD_PRE_AIC", ""),
    },
    "seg-agent-001": {
        "name": "癌症组织切片分割智能体",
        "capability": "肿瘤区域分割",
        "url": f"http://localhost:{os.getenv('SEG_AGENT_PORT', '8013')}/analyze",
        "aic": os.getenv("SEG_AGENT_AIC", ""),
    },
    "alnpre-agent-001": {
        "name": "癌症组织切片淋巴结转移预测智能体",
        "capability": "淋巴结转移预测",
        "url": f"http://localhost:{os.getenv('ALNPRE_AGENT_PORT', '8014')}/analyze",
        "aic": os.getenv("ALNPRE_AGENT_AIC", ""),
    },
}


# ---------------------------------------------------------------------------
# 路由注册（模块级函数）
# ---------------------------------------------------------------------------
def register_routes(app: FastAPI, service: "WsiLeaderService"):
    @app.get("/")
    async def _root():
        return RedirectResponse(url="/app/")

    @app.post("/upload")
    async def upload_image(file: UploadFile = File(...)):
        if not file.content_type or not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="仅支持图片文件")
        _, ext = os.path.splitext(file.filename or "")
        filename = f"{uuid.uuid4().hex}{ext or '.png'}"
        save_path = os.path.join(UPLOAD_DIR, filename)
        content = await file.read()
        with open(save_path, "wb") as fp:
            fp.write(content)
        return {"path": save_path, "url": f"/upload/{filename}"}

    @app.post("/coordinate")
    async def coordinate(payload: CoordinationPayload):
        session_id = payload.session_id or f"session_{uuid.uuid4()}"
        response = await service.handle_coordination(payload, session_id)
        return JSONResponse(response)

    @app.post("/coordinate-async")
    async def coordinate_async(payload: CoordinationPayload):
        session_id = payload.session_id or f"session_{uuid.uuid4()}"
        session = service._get_or_create_session(session_id)

        # ★ 幂等：已有 task 在跑，直接返回，不重复起协程
        existing_task = session.get("_task")
        if existing_task is not None and not existing_task.done():
            return JSONResponse({
                "session_id": session_id,
                "status": "processing",
                "message": "请求正在处理中，请勿重复提交",
            })

        # 重置状态
        session["status"] = "processing"
        session["interaction_logs"] = []
        session["error"] = None

        # 起后台 task 并保存引用
        task = asyncio.create_task(
            service._handle_coordination_async(payload, session_id)
        )
        session["_task"] = task

        service._add_interaction_log(session_id, "request_started", {
            "user_request": payload.message,
            "wsi_path": payload.wsi_path,
        })

        return JSONResponse({
            "session_id": session_id,
            "status": "processing",
            "message": "请求已接收，正在处理中...",
        })

    @app.get("/agents")
    async def list_agents():
        return {"agents": list(service.known_agents.values()),
                "total": len(service.known_agents)}

    @app.get("/sessions")
    async def list_sessions():
        return {"sessions": [
            {"session_id": sid, "messages": len(data["messages"]),
             "context": data["context"], "created_at": data["created_at"]}
            for sid, data in service.sessions.items()
        ]}

    @app.get("/sessions/{session_id}")
    async def get_session(session_id: str):
        session = service.sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    @app.get("/sessions/{session_id}/status")
    async def get_session_status(session_id: str):
        session = service.sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": session.get("status", "unknown")}

    @app.get("/sessions/{session_id}/interaction-logs/latest")
    async def get_interaction_logs(session_id: str, since: Optional[str] = None):
        session = service.sessions.get(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        logs = session.get("interaction_logs", [])
        if since:
            try:
                since_time = datetime.fromisoformat(since.replace('Z', '+00:00'))
                logs = [log for log in logs
                        if datetime.fromisoformat(
                            log["timestamp"].replace('Z', '+00:00')) > since_time]
            except ValueError:
                pass
        return {"logs": logs}

    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "known_agents": len(service.known_agents),
            "sessions": len(service.sessions),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


# ---------------------------------------------------------------------------
# Leader Service
# ---------------------------------------------------------------------------
class WsiLeaderService:
    def __init__(self):
        self.logger = logging.getLogger("WsiLeaderService")
        logging.basicConfig(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

        self.sessions: Dict[str, Dict[str, Any]] = {}
        self.known_agents: Dict[str, Dict[str, Any]] = {
            agent_id: {
                "agent_id": agent_id,
                "name": cfg["name"],
                "capability": cfg["capability"],
                "url": cfg["url"],
            }
            for agent_id, cfg in AGENT_CONFIG.items()
        }

        os.makedirs(UPLOAD_DIR, exist_ok=True)
        os.makedirs(SEG_RESULT_DIR, exist_ok=True)

        self._init_openai()
        self.app = self._init_app()

        self.acps_client = AcpsLeaderClient(
            leader_aic=ACPS_AIC,
            mq_config=_mq_config_fn(),
        )

    def _init_openai(self):
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
        self.client = openai.OpenAI(api_key=api_key, base_url=base_url)

    def _init_app(self) -> FastAPI:
        app = FastAPI(
            title="任务协调智能体",
            description="任务协调智能体，接受用户指令并智能协调其他专业智能体完成组织切片分析任务。",
            version="1.0.0",
        )
        app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_credentials=False,
            allow_methods=["*"], allow_headers=["*"],
        )
        app.mount("/upload", StaticFiles(directory=UPLOAD_DIR), name="upload")
        app.mount("/wsi-result", StaticFiles(directory=SEG_RESULT_DIR), name="wsi_result")
        register_routes(app, self)
        if os.path.isdir(CLIENT_WEB_DIR):
            app.mount("/app",
                      StaticFiles(directory=CLIENT_WEB_DIR, html=True), name="web")
        return app

    # ------------------------------------------------------------------
    async def handle_coordination(self, payload, session_id) -> Dict[str, Any]:
        message = payload.message.strip()
        session = self._get_or_create_session(session_id)
        if payload.wsi_path:
            resolved_path = self._resolve_wsi_path(payload.wsi_path)
            session["context"]["wsi_path"] = resolved_path
            if resolved_path and resolved_path not in message:
                message = f"{message}\n{resolved_path}".strip()
        self._add_message(session_id, "user", message)

        analysis = await self.analyze_request(message, session_id)
        results = await self.run_agents(analysis, session_id, message,
                                        log_interactions=False)
        final_response = await self.integrate_results(results, message, session_id)

        self._add_message(session_id, "assistant", final_response)
        session["last_analysis"] = analysis
        return {
            "session_id": session_id,
            "analysis": analysis,
            "agent_results": results,
            "final_response": final_response,
        }

    async def _handle_coordination_async(self, payload, session_id):
        try:
            message = payload.message.strip()
            session = self._get_or_create_session(session_id)
            if payload.wsi_path:
                resolved_path = self._resolve_wsi_path(payload.wsi_path)
                session["context"]["wsi_path"] = resolved_path
                if resolved_path and resolved_path not in message:
                    message = f"{message}\n{resolved_path}".strip()

            self._add_message(session_id, "user", message)
            self._add_interaction_log(session_id, "analysis_start",
                                      {"user_request": message})

            analysis = await self.analyze_request(message, session_id)
            self._add_interaction_log(session_id, "analysis_result", {
                "tissue_type": analysis.get("tissue_type"),
                "task_priority": analysis.get("task_priority", []),
                "coordination_strategy": analysis.get("coordination_strategy", ""),
            })
            self._add_interaction_log(session_id, "coordination_start", {
                "agents_to_contact": analysis.get("task_priority", []),
            })

            results = await self.run_agents(analysis, session_id, message,
                                            log_interactions=True)

            self._add_interaction_log(session_id, "integration_start", {
                "integration_prompt": "开始整合智能体结果...",
            })
            final_response = await self.integrate_results(results, message, session_id)
            self._add_interaction_log(session_id, "integration_complete",
                                      {"final_response": final_response})

            self._add_message(session_id, "assistant", final_response)
            session["last_analysis"] = analysis
            session["last_artifacts"] = self._extract_artifacts(results)
            session["status"] = "completed"
            self._add_interaction_log(session_id, "request_completed",
                                      {"final_response": final_response})
        except Exception as exc:
            self.logger.error(f"异步协调处理失败: {exc}", exc_info=True)
            session = self.sessions.get(session_id, {})
            session["status"] = "error"
            session["error"] = str(exc)
            self._add_interaction_log(session_id, "request_error", {"error": str(exc)})
        finally:
            # ★ 释放 task 引用，允许后续重试
            session = self.sessions.get(session_id)
            if session is not None:
                session["_task"] = None
                
    # ------------------------------------------------------------------
    async def analyze_request(self, message: str, session_id: str) -> Dict[str, Any]:
        stripped = (message or "").strip()
        stripped_no_path = re.sub(
            r"/[\S]+\.(?:wsi|svs|tif|tiff|ndpi|mrxs|jpg|jpeg|png)",
            "", stripped, flags=re.IGNORECASE,
        ).strip()
        stripped_no_path = re.sub(
            r"(请分析这张切片|请分析|分析这张切片|分析)", "",
            stripped_no_path,
        ).strip()

        if not stripped_no_path:
            self.logger.info("[analyze_request] 空文本/纯图片请求，使用默认完整流程")
            return {
                "tissue_type": "unknown",
                "user_needs": {},
                "task_priority": self._select_agents({"tissue_type": "unknown"}),
                "coordination_strategy": "默认完整流程：预测 → 分割 → 淋巴结预测",
            }

        agents_info = "\n".join(
            f"- {aid}: {info['capability']}"
            for aid, info in self.known_agents.items()
        ) or "当前无可用智能体"

        prompt = f"""你是多智能体医疗协作系统的调度助手，请分析以下病理切片请求，确定需要的智能体以及执行顺序。

=== 既往对话片段 ===
{self._get_session_context(session_id)}

=== 当前用户请求 ===
{message}

=== 可用智能体 ===
{agents_info}

在设计 task_priority 时，请遵循以下逻辑（除非用户在请求中有明确的特殊要求）：
1. 如果是乳腺切片 (breast)：
   - 优先调用 乳腺组织切片预测智能体 (brca-pre-agent-001)，判断是否为癌症。
   - 如果预测结果为恶性/可疑，再调用 切片分割智能体 (seg-agent-001)。
   - 在已经有分割结果的前提下，如有需要再调用 淋巴结转移预测智能体 (alnpre-agent-001)。
2. 如果是结肠切片 (colon)：
   - 类似流程：先 coad-pre-agent-001，再 seg-agent-001，必要时再 alnpre-agent-001。
3. 如果用户只明确提出"只做分割"或"只做预测"，可以只安排相关智能体。

请输出 JSON，包含字段：
{{
  "tissue_type": "breast|colon|unknown",
  "user_needs": {{"wsi_path": "...", "patient_id": "..."}},
  "task_priority": ["agent_id1", "agent_id2", ...],
  "coordination_strategy": "自然语言描述"
}}
"""
        try:
            resp = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=os.getenv("OPENAI_MODEL"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1200,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```"):
                content = re.sub(r"^```[a-zA-Z0-9_-]*", "", content.strip())
                content = re.sub(r"```$", "", content.strip())
            try:
                analysis = json.loads(content)
            except json.JSONDecodeError as exc:
                preview = content[:400]
                self.logger.error("LLM 返回结果非JSON: %s", preview)
                raise
        except openai.RateLimitError as exc:
            self.logger.error("LLM API配额已用完: %s", exc)
            raise Exception(f"LLM服务配额已用完: {exc}")
        except openai.APIError as exc:
            self.logger.error("LLM API错误: %s", exc)
            raise Exception(f"LLM服务调用失败: {exc}")
        except Exception as exc:
            self.logger.error("LLM 分析失败: %s", exc)
            raise Exception(f"请求分析失败: {exc}")

        if not analysis.get("task_priority"):
            analysis["task_priority"] = self._select_agents(analysis)

        tp = list(analysis.get("task_priority") or [])
        if ("seg-agent-001" in tp
                and "alnpre-agent-001" not in tp
                and "alnpre-agent-001" in self.known_agents):
            tp.append("alnpre-agent-001")

        if "seg-agent-001" in tp and not any(
            a in tp for a in ("brca-pre-agent-001", "coad-pre-agent-001")
        ):
            tp = ["brca-pre-agent-001", "coad-pre-agent-001"] + [
                a for a in tp
                if a not in ("brca-pre-agent-001", "coad-pre-agent-001")
            ]

        analysis["task_priority"] = [a for a in tp if a in self.known_agents]
        self.logger.info("[analyze_request] final task_priority=%s, tissue_type=%s",
                         analysis.get("task_priority"), analysis.get("tissue_type"))
        return analysis

    def _select_agents(self, analysis: Dict[str, Any]) -> List[str]:
        tissue = (analysis.get("tissue_type") or "").lower()
        order = []
        if tissue == "breast":
            order.append("brca-pre-agent-001")
        elif tissue == "colon":
            order.append("coad-pre-agent-001")
        else:
            order.extend(["brca-pre-agent-001", "coad-pre-agent-001"])
        order.extend(["seg-agent-001", "alnpre-agent-001"])
        return [aid for aid in order if aid in self.known_agents]

    def _resolve_wsi_path(self, path: Optional[str]) -> Optional[str]:
        if not path:
            return None
        candidate = str(path).strip()
        if not candidate:
            return None
        candidate = os.path.normpath(candidate)
        if candidate.startswith("/upload/"):
            return os.path.normpath(os.path.join(UPLOAD_DIR, os.path.basename(candidate)))
        if os.path.isabs(candidate):
            return candidate
        filename = os.path.basename(candidate)
        if not filename or filename in {".", ".."}:
            return None
        return os.path.normpath(os.path.join(UPLOAD_DIR, filename))

    # ------------------------------------------------------------------
    # 合并后的 run_agents
    # ------------------------------------------------------------------
    async def run_agents(self, analysis, session_id, user_message,
                         *, log_interactions: bool = False) -> Dict[str, Any]:
        results: Dict[str, Any] = {}

        def _log(step, data):
            if log_interactions:
                self._add_interaction_log(session_id, step, data)

        # 解析 wsi_path
        user_needs = analysis.get("user_needs", {})
        wsi_path = user_needs.get("wsi_path")
        session = self._get_or_create_session(session_id)
        if not wsi_path and session["context"].get("wsi_path"):
            wsi_path = session["context"]["wsi_path"]

        if not wsi_path:
            match = re.search(
                r"(/[\S]+\.(?:wsi|svs|tif|tiff|ndpi|mrxs|jpg|jpeg|png))",
                user_message, re.IGNORECASE,
            )
            wsi_path = match.group(1) if match else None
        wsi_path = self._resolve_wsi_path(wsi_path)

        if not wsi_path:
            _log("coordination_skipped", {"reason": "no wsi_path resolved"})
            self.logger.warning("[run_agents] 未解析到切片路径")
            return results

        task_order = analysis.get("task_priority", [])
        predictors = [a for a in task_order
                      if a in ("brca-pre-agent-001", "coad-pre-agent-001")]
        need_seg = "seg-agent-001" in task_order
        need_alnpre = "alnpre-agent-001" in task_order

        all_agent_ids = list(predictors)
        if need_seg:
            all_agent_ids.append("seg-agent-001")
        if need_alnpre:
            all_agent_ids.append("alnpre-agent-001")

        all_aics = [AGENT_CONFIG[a]["aic"] for a in all_agent_ids
                    if AGENT_CONFIG.get(a, {}).get("aic")]
        if not all_aics:
            _log("coordination_skipped", {"reason": "no AIC configured"})
            return results

        async with self.acps_client.open_group(session_id, all_aics) as ctx:
            # ★ 只对成功加入群组的 agent 发任务
            joined = ctx.joined_aics()
            skipped_aics = {AGENT_CONFIG[a]["aic"] for a in all_agent_ids
                            if AGENT_CONFIG[a]["aic"] not in joined}

            if ctx.pending_partners:
                self.logger.warning(
                    "[run_agents] 以下 agent 未加入群组，跳过: %s",
                    [a[-8:] for a in ctx.pending_partners],
                )
                for agent_id in all_agent_ids:
                    aic = AGENT_CONFIG.get(agent_id, {}).get("aic")
                    if aic in ctx.pending_partners:
                        results[agent_id] = {
                            "success": False,
                            "error": f"agent 未加入群组 (pending)",
                        }

            # ---- 过滤：只保留已加入的 agent ----
            active_agent_ids = [
                aid for aid in all_agent_ids
                if AGENT_CONFIG.get(aid, {}).get("aic") not in ctx.pending_partners
            ]
            if not active_agent_ids:
                self.logger.error("[run_agents] 没有任何 agent 可用，终止")
                return results

            task_order = analysis.get("task_priority", [])
            predictors = [a for a in active_agent_ids
                        if a in ("brca-pre-agent-001", "coad-pre-agent-001")]
            need_seg = "seg-agent-001" in active_agent_ids
            need_alnpre = "alnpre-agent-001" in active_agent_ids

            # ========== 阶段 1：预测（全并行） ==========
            if predictors:
                await self._run_predictors(ctx, predictors, wsi_path,
                                        results, session_id, _log)

            is_cancer = self._extract_is_cancer(results, predictors)
            if is_cancer is False:
                _log("agent_skipped_benign", {
                    "reason": "predictors returned benign",
                    "skipped": ["seg-agent-001", "alnpre-agent-001"],
                })
                return results

            # ========== 阶段 2：分割 ==========
            transparent_path = None
            if need_seg:
                seg_text = f"请对切片做肿瘤区域分割，切片路径：{wsi_path}"
                _log("agent_request", {
                    "agent_id": "seg-agent-001",
                    "agent_name": self.known_agents.get("seg-agent-001", {}).get("name", ""),
                    "request_content": seg_text,
                })
                seg_res = await self.acps_client.send_task(
                    ctx, AGENT_CONFIG["seg-agent-001"]["aic"],
                    text_hint=seg_text, timeout=TIMEOUT_SEG,
                )
                results["seg-agent-001"] = seg_res
                _log("agent_response_success" if seg_res.get("success")
                    else "agent_response_error", {
                    "agent_id": "seg-agent-001",
                    "agent_name": self.known_agents.get("seg-agent-001", {}).get("name", ""),
                    "response_content": (seg_res.get("text") or "")[:200],
                    "error": seg_res.get("error"),
                })
                if not seg_res.get("success"):
                    return results

                structured = seg_res.get("structured") or {}
                transparent_path = (
                    self.find_transparent_path(structured)
                    or self.find_transparent_path(seg_res.get("text", ""))
                )

            # ========== 阶段 3：淋巴结 ==========
            if need_alnpre:
                if not transparent_path:
                    results["alnpre-agent-001"] = {
                        "success": False,
                        "error": "seg 未返回 transparent_path",
                    }
                    _log("agent_response_error", {
                        "agent_id": "alnpre-agent-001",
                        "agent_name": self.known_agents.get("alnpre-agent-001", {}).get("name", ""),
                        "error": "seg 未返回 transparent_path",
                    })
                else:
                    alnpre_text = f"请基于分割透明图预测淋巴结转移，透明图路径：{transparent_path}"
                    _log("agent_request", {
                        "agent_id": "alnpre-agent-001",
                        "agent_name": self.known_agents.get("alnpre-agent-001", {}).get("name", ""),
                        "request_content": alnpre_text,
                    })
                    alnpre_res = await self.acps_client.send_task(
                        ctx, AGENT_CONFIG["alnpre-agent-001"]["aic"],
                        text_hint=alnpre_text, timeout=TIMEOUT_ALNPRE,
                    )
                    results["alnpre-agent-001"] = alnpre_res
                    _log("agent_response_success" if alnpre_res.get("success")
                        else "agent_response_error", {
                        "agent_id": "alnpre-agent-001",
                        "agent_name": self.known_agents.get("alnpre-agent-001", {}).get("name", ""),
                        "response_content": (alnpre_res.get("text") or "")[:200],
                        "error": alnpre_res.get("error"),
                    })

        return results

    async def _run_predictors(self, ctx, predictors, wsi_path, results,
                            session_id, _log):
        # ★ 双重防御：即便传进来也再滤一遍
        valid = [
            a for a in predictors
            if AGENT_CONFIG.get(a, {}).get("aic")
            and AGENT_CONFIG[a]["aic"] not in ctx.pending_partners
        ]
        if not valid:
            self.logger.warning("[_run_predictors] 无可用 predictor，跳过")
            return

        text = f"请判断这张切片是否癌变，切片路径：{wsi_path}"
        _log("agent_request", {
            "stage": "predictors",
            "agents": [
                {"agent_id": aid,
                "agent_name": self.known_agents.get(aid, {}).get("name", aid)}
                for aid in valid
            ],
            "request_content": text,
        })

        send_coros = [
            self.acps_client.send_task_no_wait(
                ctx, AGENT_CONFIG[aid]["aic"], text_hint=text,
            )
            for aid in valid
        ]
        try:
            task_ids = await asyncio.gather(*send_coros)
        except Exception as exc:
            self.logger.exception("[_run_predictors] 并发发任务失败: %s", exc)
            for aid in valid:
                results[aid] = {"success": False, "error": str(exc)}
            return

        wait_coros = [
            self.acps_client.wait_for_result(
                ctx, tid, AGENT_CONFIG[aid]["aic"], timeout=TIMEOUT_PREDICTOR,
            )
            for tid, aid in zip(task_ids, valid)
        ]
        res_list = await asyncio.gather(*wait_coros, return_exceptions=True)

        for aid, res in zip(valid, res_list):
            if isinstance(res, Exception):
                self.logger.error("[_run_predictors] %s 异常: %r", aid, res)
                results[aid] = {"success": False, "error": str(res)}
            else:
                results[aid] = res
            r = results[aid]
            _log("agent_response_success" if r.get("success")
                else "agent_response_error", {
                "agent_id": aid,
                "agent_name": self.known_agents.get(aid, {}).get("name", aid),
                "response_content": (r.get("text") or "")[:200],
                "error": r.get("error"),
            })

    def _extract_is_cancer(self, results, predictors) -> Optional[bool]:
        if not predictors:
            return None
        valid_predictions: List[int] = []
        for aid in predictors:
            r = results.get(aid) or {}
            if not r.get("success"):
                continue
            structured = r.get("structured") or self._try_parse_json(r.get("text"))
            if not isinstance(structured, dict):
                continue
            pred = structured.get("prediction")
            if not isinstance(pred, int):
                continue
            if pred not in (0, 1):
                self.logger.warning(
                    "[_extract_is_cancer] %s 非法 prediction=%s，忽略", aid, pred)
                continue
            valid_predictions.append(pred)

        if not valid_predictions:
            self.logger.warning("[_extract_is_cancer] 无有效预测，返回 None")
            return None
        if any(p == 0 for p in valid_predictions):
            return True
        if all(p == 1 for p in valid_predictions):
            return False
        return None

    # ------------------------------------------------------------------
    async def integrate_results(self, results, user_message, session_id) -> str:
        slim_results = {}
        for k, v in results.items():
            if isinstance(v, dict):
                slim_results[k] = {kk: vv for kk, vv in v.items() if kk != "file_items"}
            else:
                slim_results[k] = v

        prompt = f"""整合以下智能体结果，为用户生成病理切片分析报告。

=== 用户请求 ===
{user_message}

=== 智能体返回 ===
{json.dumps(slim_results, ensure_ascii=False, indent=2)}

请以中文输出：
1. 总结（是否存在癌变及置信度）
2. 分割发现（描述主要病灶区域，不要直接暴露路径）
3. 淋巴结转移预测
4. 下一步建议
"""
        try:
            resp = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=os.getenv("OPENAI_MODEL"),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4, max_tokens=1000,
            )
            return resp.choices[0].message.content.strip()
        except (openai.RateLimitError, openai.APIError) as exc:
            self.logger.warning("整合失败，回退: %s", exc)
            return self._build_fallback_report(results)
        except Exception as exc:
            self.logger.warning("整合失败，回退: %s", exc)
            return self._build_fallback_report(results)

    def _build_fallback_report(self, results) -> str:
        lines = ["病理分析简要报告（回退模式）："]
        for agent_id, result in results.items():
            status = "成功" if result.get("success") else "失败"
            detail = result.get("text") or result.get("error", "")
            lines.append(f"- {agent_id}: {status} {detail}")
        lines.append("建议：请人工复核上述结果，并结合临床信息进一步分析。")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    def _get_or_create_session(self, session_id: str) -> Dict[str, Any]:
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "messages": [],
                "context": {"wsi_path": None, "patient_id": None},
                "last_analysis": None,
            }
        return self.sessions[session_id]

    def _add_message(self, session_id, role, content):
        session = self._get_or_create_session(session_id)
        session["messages"].append({
            "role": role, "content": content,
            "timestamp": datetime.now().isoformat(),
        })

    def _get_session_context(self, session_id) -> str:
        session = self._get_or_create_session(session_id)
        history = session["messages"][-5:]
        context_lines = [f"{msg['role']}: {msg['content']}"
                         for msg in history if msg["content"]]
        ctx = session["context"]
        if ctx.get("wsi_path"):
            context_lines.append(f"切片路径: {ctx['wsi_path']}")
        if ctx.get("patient_id"):
            context_lines.append(f"患者ID: {ctx['patient_id']}")
        return "\n".join(context_lines)

    def find_transparent_path(self, data: Any) -> Optional[str]:
        if isinstance(data, dict):
            candidate = data.get("transparent_path")
            if isinstance(candidate, str):
                return candidate
            for value in data.values():
                path = self.find_transparent_path(value)
                if path:
                    return path
        if isinstance(data, list):
            for value in data:
                path = self.find_transparent_path(value)
                if path:
                    return path
        if isinstance(data, str):
            match = re.search(
                r"(/[\S]*transparent[\S]*\.(?:png|jpg|jpeg|tif|tiff))",
                data, re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None

    def _find_contour_path(self, data: Any) -> Optional[str]:
        if isinstance(data, dict):
            candidate = data.get("contour_path")
            if isinstance(candidate, str):
                return candidate
            for value in data.values():
                path = self._find_contour_path(value)
                if path:
                    return path
        if isinstance(data, list):
            for value in data:
                path = self._find_contour_path(value)
                if path:
                    return path
        if isinstance(data, str):
            match = re.search(
                r"(/[\S]*contour[\S]*\.(?:png|jpg|jpeg|tif|tiff))",
                data, re.IGNORECASE,
            )
            if match:
                return match.group(1)
        return None

    def _try_parse_json(self, payload: Optional[str]) -> Optional[Any]:
        if not payload:
            return None
        try:
            return json.loads(payload)
        except Exception:
            return None

    def _add_interaction_log(self, session_id, step, data):
        session = self._get_or_create_session(session_id)
        if "interaction_logs" not in session:
            session["interaction_logs"] = []
        log_entry = {
            "id": f"log-{len(session['interaction_logs'])}-{int(datetime.now().timestamp() * 1000)}",
            "step": step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        session["interaction_logs"].append(log_entry)

    def _extract_artifacts(self, results) -> Dict[str, Any]:
        artifacts = {}
        for agent_id, result in results.items():
            if agent_id == "seg-agent-001" and result.get("success"):
                structured = result.get("structured", {})
                text = result.get("text", "")
                transparent_path = (self.find_transparent_path(structured)
                                    or self.find_transparent_path(text))
                if transparent_path:
                    artifacts["transparent_path"] = transparent_path
                    artifacts["transparent_url"] = transparent_path.replace(_PROJECT_ROOT, "")
                contour_path = (self._find_contour_path(structured)
                                or self._find_contour_path(text))
                if contour_path:
                    artifacts["contour_path"] = contour_path
                    artifacts["contour_url"] = contour_path.replace(_PROJECT_ROOT, "")
        return artifacts


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from acps_config import silence_noisy_loggers

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    silence_noisy_loggers()   # ← 新增

    service = WsiLeaderService()
    MQ_CONFIG = _mq_config_fn()

    async def _start_all():
        partner = await start_acps_group_partner(service, ACPS_AIC, MQ_CONFIG)
        await service.acps_client.start()

        config = uvicorn.Config(service.app, host="0.0.0.0", port=HTTP_PORT)
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await service.acps_client.close()
            await partner.close()

    asyncio.run(_start_all())