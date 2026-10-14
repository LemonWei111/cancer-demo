#!/usr/bin/env python3
# partners/coad-pre-agent/server-api/main.py
"""结肠组织切片预测智能体 — ACPs 群组 Partner。"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "common")
))
from acps_subagent_adapter import AcpsSubAgentPartner
from acps_config import mq_config as _mq_config_fn, silence_noisy_loggers

from predict import classify_wsi

load_dotenv()

AGENT_NAME = os.getenv("AGENT_NAME", "结肠组织切片预测智能体")
ACPS_AIC = os.getenv("ACPS_PARTNER_AIC", "")
MQ_CONFIG = _mq_config_fn()


def analyze_func(local_image_path: str) -> Any:
    is_positive, score = classify_wsi(local_image_path)
    return {
        "prediction": 0 if is_positive else 1,
        "label": "tumor" if is_positive else "normal",
        "confidence": score,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def result_adapter(result: Any) -> Dict[str, Any]:
    payload = result if isinstance(result, dict) else {"raw": result}
    return {"text": json.dumps(payload, ensure_ascii=False), "files": []}


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