#!/usr/bin/env python3
# partners/brca-pre-agent/server-api/main.py
"""乳腺组织切片预测智能体 — ACPs 群组 Partner。"""
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "common")
))
from acps_subagent_adapter import AcpsSubAgentPartner
from acps_config import mq_config as _mq_config_fn, silence_noisy_loggers

from predict_breast_cancer import predict_breast_cancer

load_dotenv()

logger = logging.getLogger("BrcaAgent")

AGENT_NAME = os.getenv("AGENT_NAME", "乳腺组织切片预测智能体")
ACPS_AIC = os.getenv("ACPS_PARTNER_AIC", "")
MQ_CONFIG = _mq_config_fn()


def analyze_func(local_image_path: str) -> Any:
    logger.info("analyze path=%s exists=%s",
                local_image_path, os.path.exists(local_image_path))
    return predict_breast_cancer(local_image_path)


def result_adapter(result: Any) -> Dict[str, Any]:
    payload = dict(result) if isinstance(result, dict) else {"raw": result}

    if "prediction" not in payload:
        if "is_cancer" in payload:
            payload["prediction"] = 0 if payload["is_cancer"] else 1
        elif isinstance(payload.get("label"), str):
            payload["prediction"] = (
                0 if payload["label"].lower() in ("tumor", "malignant") else 1
            )

    if "label" not in payload and "prediction" in payload:
        if payload["prediction"] == 0:
            payload["label"] = "tumor"
        elif payload["prediction"] == 1:
            payload["label"] = "normal"
        else:
            payload["label"] = "unknown"

    if payload.get("prediction") == -1:
        logger.warning("prediction=-1, message=%r", payload.get("message"))

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