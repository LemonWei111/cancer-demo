#!/usr/bin/env python3
# partners/seg-agent/server-api/main.py
"""癌症组织切片分割智能体 — ACPs 群组 Partner。"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "common")
))
from acps_subagent_adapter import AcpsSubAgentPartner
from acps_config import mq_config as _mq_config_fn, silence_noisy_loggers
from acps_utils import compress_image_to_b64, force_ext   # ← 新增 import

from integrated_wsi_pipeline import process_entry, format_output_text

load_dotenv()

logger = logging.getLogger("SegAgent")

AGENT_NAME = os.getenv("AGENT_NAME", "癌症组织切片分割智能体")
ACPS_AIC = os.getenv("ACPS_PARTNER_AIC", "")
MQ_CONFIG = _mq_config_fn()

THUMB_MAX_SIZE = int(os.getenv("SEG_THUMB_MAX_SIZE", "800"))
THUMB_QUALITY = int(os.getenv("SEG_THUMB_QUALITY", "85"))


def analyze_func(local_image_path: str) -> Any:
    return process_entry(local_image_path)


def _find_paths(data: Any) -> Dict[str, str]:
    found: Dict[str, str] = {}
    KEY_ALIASES = {
        "transparent_path": "transparent_path",
        "transparent": "transparent_path",
        "contour_path": "contour_path",
        "contour": "contour_path",
    }

    def _walk(d: Any):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in KEY_ALIASES and isinstance(v, str):
                    found[KEY_ALIASES[k]] = v
                else:
                    _walk(v)
        elif isinstance(d, list):
            for v in d:
                _walk(v)

    _walk(data)
    return found


def result_adapter(result: Any) -> Dict[str, Any]:
    try:
        summary = format_output_text(result)
    except Exception:
        summary = ""

    payload = {
        "summary": summary,
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    paths = _find_paths(result)
    files: List[Dict[str, Any]] = []

    for key in ("contour_path", "transparent_path"):
        p = paths.get(key)
        if not p or not os.path.isfile(p):
            logger.warning("[seg] 未找到 %s", key)
            continue

        b64: Optional[str] = None
        mime = "application/octet-stream"
        try:
            b64, mime = compress_image_to_b64(
                p, max_size=THUMB_MAX_SIZE, quality=THUMB_QUALITY,
            )
            logger.info(
                "[seg] 压缩 %s: %d bytes → %d chars b64 (mime=%s)",
                key, os.path.getsize(p), len(b64), mime,
            )
        except Exception as exc:
            logger.warning("[seg] 压缩 %s 失败: %s", key, exc)
            # 失败时降级：读原文件，mime 用原后缀猜的
            try:
                with open(p, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
                ext = os.path.splitext(p)[1].lower()
                mime = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".tif": "image/tiff",
                    ".tiff": "image/tiff",
                }.get(ext, "application/octet-stream")
            except Exception as exc2:
                logger.error("[seg] 读原图也失败 %s: %s", key, exc2)
                continue

        # ★ 关键：用 force_ext 保证 name 后缀与 mime 一致
        files.append({
            "name": force_ext(os.path.basename(p), mime),
            "mime": mime,
            "uri": p,
            "path": None,
            "bytes_b64": b64,
        })

    logger.info("[seg] result_adapter 输出 files=%d 个", len(files))
    return {"text": json.dumps(payload, ensure_ascii=False), "files": files}


async def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    silence_noisy_loggers()   # ← 新增：压制 SDK 信息性 warning
    partner = AcpsSubAgentPartner(
        agent_name=AGENT_NAME,
        partner_aic=ACPS_AIC,
        mq_config=MQ_CONFIG,
        analyze_func=analyze_func,
        result_adapter=result_adapter,
    )
    await partner.start()
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await partner.close()


if __name__ == "__main__":
    asyncio.run(main())