#!/usr/bin/env python3
# partners/alnpre-agent/server-api/main.py
"""癌症组织切片淋巴结转移预测智能体 — ACPs 群组 Partner。"""
import asyncio
import json
import logging
import os
import sys
from typing import Any, Dict, List

from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "common")
))
from acps_subagent_adapter import AcpsSubAgentPartner
from acps_config import mq_config as _mq_config_fn, silence_noisy_loggers

from infer import alnpre_predict_auto

load_dotenv()

logger = logging.getLogger("AlnpreAgent")

AGENT_NAME = os.getenv("AGENT_NAME", "癌症组织切片淋巴结转移预测智能体")
ACPS_AIC = os.getenv("ACPS_PARTNER_AIC", "")
MQ_CONFIG = _mq_config_fn()


def analyze_func(local_image_path: str) -> Any:
    return alnpre_predict_auto(local_image_path)


def result_adapter(result: Any) -> Dict[str, Any]:
    if not isinstance(result, dict):
        return {"text": str(result), "files": []}

    payload = {
        "results": result.get("results", []),
        "aggregate_csv": result.get("aggregate_csv", ""),
        "summary_text": result.get("summary_text", ""),
    }

    files: List[Dict[str, Any]] = []
    csv_path = payload.get("aggregate_csv")
    if isinstance(csv_path, str) and os.path.isfile(csv_path):
        files.append({
            "name": os.path.basename(csv_path),
            "mime": "text/csv",
            "path": csv_path,
            "uri": "",
        })

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