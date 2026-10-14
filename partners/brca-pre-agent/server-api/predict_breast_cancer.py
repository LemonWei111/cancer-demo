# partners/brca-pre-agent/server-api/predict_breast_cancer.py
"""
乳腺癌预测器（brcapre-dev 环境）：
- 不直接使用 Caffe
- 通过子进程调用 caffe-py38 环境中的 breast_caffe_infer.py

日志策略：
- 子进程 stderr 在**成功**时只可能是第三方库的 warning/progress，
  一律降为 debug，避免污染生产日志；
- 只有 returncode != 0 时，stderr 才升为 error，方便排查。
"""
import json
import logging
import os
import subprocess
from typing import Dict, Union

logger = logging.getLogger("BrcaPredict")


def _error(message: str) -> Dict[str, Union[float, int, str]]:
    logger.error("[predict_breast_cancer] %s", message)
    return {
        "prediction": -1,
        "confidence": 0.0,
        "probabilities": [0.0, 0.0],
        "global_probabilities": [0.0, 0.0],
        "patch_probabilities": [0.0, 0.0],
        "status": "error",
        "message": message,
    }


def predict_breast_cancer(image_path: str) -> Dict[str, Union[float, int, str]]:
    """
    在 brcapre-dev 环境中，通过子进程调用 caffe-py38 环境完成乳腺癌预测。
    """
    logger.info("[predict_breast_cancer] image_path=%r", image_path)

    if not image_path:
        return _error("图像路径为空")

    if not os.path.exists(image_path):
        return _error(f"图像文件不存在: {image_path}")

    logger.info(
        "[predict_breast_cancer] 文件存在，size=%d bytes",
        os.path.getsize(image_path),
    )

    base_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(base_dir, "breast_caffe_infer.py")
    if not os.path.exists(script_path):
        return _error(f"未找到 Caffe 推理脚本: {script_path}")

    cmd = [
        "conda",
        "run",
        "--no-capture-output",
        "-n",
        "caffe-py38",
        "python",
        "-s",
        script_path,
        image_path,
    ]
    logger.info("[predict_breast_cancer] cmd=%s", " ".join(cmd))

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env["GLOG_minloglevel"] = "2"
    # ★ 子进程里压制所有 Python 层 DeprecationWarning（含 skimage as_grey）
    env["PYTHONWARNINGS"] = "ignore::DeprecationWarning"
    env.setdefault("PYTHONUNBUFFERED", "1")

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
    except FileNotFoundError as e:
        return _error(f"无法执行 conda 命令，请确认 Anaconda 已安装并在 PATH 中: {e}")
    except Exception as e:
        return _error(f"调用 Caffe 子进程失败: {e}")

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    # ★ 关键：stderr 分级
    if result.returncode != 0:
        logger.error(
            "[predict_breast_cancer] 子进程失败 rc=%s stderr=%s",
            result.returncode, stderr[:1000] or "(empty)",
        )
    else:
        # 成功时 stderr 里通常是第三方库 warning / 进度条，降为 debug
        if stderr:
            logger.debug(
                "[predict_breast_cancer] 子进程 stderr（非错误）: %s",
                stderr[:500],
            )

    if stdout:
        logger.debug("[predict_breast_cancer] stdout[:500]=%s", stdout[:500])

    if result.returncode != 0 and not stdout:
        return _error(
            f"Caffe 子进程异常退出 (exit={result.returncode}): {stderr or '无错误输出'}"
        )

    try:
        parsed = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as e:
        return _error(
            f"无法解析 Caffe 子进程输出为 JSON: {e}，原始输出: {stdout[:300]}"
        )

    if isinstance(parsed, dict) and "prediction" in parsed:
        logger.info(
            "[predict_breast_cancer] 成功，prediction=%s, confidence=%s",
            parsed.get("prediction"), parsed.get("confidence"),
        )
        return parsed

    return _error(f"Caffe 子进程返回了未知格式: {stdout[:300]}")