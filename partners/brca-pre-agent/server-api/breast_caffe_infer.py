"""
在 caffe-py38 环境中运行的乳腺癌预测脚本。
职责：
- 直接使用 Caffe 和 .caffemodel 完成一次预测
- 将结果以 JSON 打印到 stdout，由上层子进程调用方解析
"""

import json
import os
import sys
from typing import Dict, Union


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 模型路径：优先读环境变量，其次用默认路径
# 修改方式：在 .env 中设置 BRCA_GLOBAL_MODEL_PATH / BRCA_PATCH_MODEL_PATH
_DEFAULT_MODEL_ROOT = os.path.expanduser("~/BreastCancerCNN-master")
GLOBAL_MODEL_PATH = os.environ.get(
    "BRCA_GLOBAL_MODEL_PATH",
    os.path.join(
        _DEFAULT_MODEL_ROOT,
        "model",
        "3_global_googlenet_iter_7000_0.929_0.21.caffemodel",
    ),
)
PATCH_MODEL_PATH = os.environ.get(
    "BRCA_PATCH_MODEL_PATH",
    os.path.join(
        _DEFAULT_MODEL_ROOT,
        "model-patch",
        "3_patch_googlenet_iter_70000_0.859_0.314.caffemodel",
    ),
)
MODEL_DEF_PATH = os.path.join(BASE_DIR, "deploy_SE_bn4.prototxt")
MEAN_FILE_PATH = os.path.join(BASE_DIR, "train_224_mean.npy")

PATCH_WEIGHT = 1.9
GLOBAL_WEIGHT = 1.0


def _error(message: str) -> Dict[str, Union[float, int, str]]:
    return {
        "prediction": -1,
        "confidence": 0.0,
        "probabilities": [0.0, 0.0],
        "global_probabilities": [0.0, 0.0],
        "patch_probabilities": [0.0, 0.0],
        "status": "error",
        "message": message,
    }


def predict_breast_cancer_caffe(image_path: str) -> Dict[str, Union[float, int, str]]:
    """在 caffe-py38 环境中使用 Caffe 执行一次乳腺癌预测。"""
    try:
        # 降低Caffe日志输出
        os.environ.setdefault("GLOG_minloglevel", "2")
        import numpy as np
        import caffe
    except ImportError as e:
        return _error(f"缺少必要的依赖包: {e}")

    # 检查文件是否存在
    if not os.path.exists(image_path):
        return _error(f"图像文件不存在: {image_path}")

    # 校验固定模型与配置文件
    if not os.path.exists(MODEL_DEF_PATH):
        return _error(f"未找到模型定义文件: {MODEL_DEF_PATH}")

    if not os.path.exists(MEAN_FILE_PATH):
        return _error(f"未找到均值文件: {MEAN_FILE_PATH}")

    if not os.path.exists(GLOBAL_MODEL_PATH):
        return _error(f"未找到全局模型: {GLOBAL_MODEL_PATH}")

    if not os.path.exists(PATCH_MODEL_PATH):
        return _error(f"未找到补丁模型: {PATCH_MODEL_PATH}")

    try:
        # 设置Caffe（强制CPU，避免 GPU/CUDA 兼容问题）
        caffe.set_mode_cpu()

        # 加载均值
        mu = json  # type: ignore[assignment]
    except Exception:
        # 为了 mypy 之类工具，这里稍作调整，实际逻辑在下面重新赋值
        pass

    try:
        import numpy as np  # type: ignore[reimported]
        import caffe  # type: ignore[reimported]

        mu = np.load(MEAN_FILE_PATH)
        mu = mu.mean(1).mean(1)

        # 1. 使用全局模型预测
        global_net = caffe.Net(MODEL_DEF_PATH, GLOBAL_MODEL_PATH, caffe.TEST)
        global_transformer = caffe.io.Transformer({"data": global_net.blobs["data"].data.shape})
        global_transformer.set_transpose("data", (2, 0, 1))
        global_transformer.set_mean("data", mu)
        global_transformer.set_raw_scale("data", 255)
        global_transformer.set_channel_swap("data", (2, 1, 0))
        global_net.blobs["data"].reshape(1, 3, 224, 224)

        # 加载和预处理图像
        image = caffe.io.load_image(image_path)
        transformed_image = global_transformer.preprocess("data", image)
        global_net.blobs["data"].data[0] = transformed_image
        global_output = global_net.forward()
        global_probabilities = global_output["prob"][0]

        # 2. 使用补丁模型预测
        patch_net = caffe.Net(MODEL_DEF_PATH, PATCH_MODEL_PATH, caffe.TEST)
        patch_transformer = caffe.io.Transformer({"data": patch_net.blobs["data"].data.shape})
        patch_transformer.set_transpose("data", (2, 0, 1))
        patch_transformer.set_mean("data", mu)
        patch_transformer.set_raw_scale("data", 255)
        patch_transformer.set_channel_swap("data", (2, 1, 0))
        patch_net.blobs["data"].reshape(1, 3, 224, 224)

        patch_net.blobs["data"].data[0] = transformed_image
        patch_output = patch_net.forward()
        patch_probabilities = patch_output["prob"][0]

        # 3. 结合两种模型的预测结果（原始顺序通常为 [良性, 恶性]）
        combined_probabilities = (
            GLOBAL_WEIGHT * global_probabilities
            + PATCH_WEIGHT * patch_probabilities
        ) / (GLOBAL_WEIGHT + PATCH_WEIGHT)

        # 4. 重映射类别顺序为要求的 [恶性(0), 良性(1)]
        remap = [0, 1]
        probabilities_out = combined_probabilities[remap]

        # 获取最终预测结果（0=恶性, 1=良性）
        prediction = int(np.argmax(probabilities_out))
        confidence = float(np.max(probabilities_out))

        label = "恶性" if prediction == 0 else "良性"

        return {
            "label": label,
            "prediction": prediction,
            "confidence": confidence,
            "probabilities": probabilities_out.tolist(),  # [恶性, 良性]
            "status": "ok",
            "message": "success",
        }
    except Exception as e:  # pragma: no cover - 运行时错误回传
        return _error(f"预测过程中发生错误: {e}")


def main() -> int:
    if len(sys.argv) != 2:
        print(
            json.dumps(
                _error("用法: python breast_caffe_infer.py <image_path>"),
                ensure_ascii=False,
            )
        )
        return 1

    image_path = sys.argv[1]
    result = predict_breast_cancer_caffe(image_path)
    print(json.dumps(result, ensure_ascii=False))
    # exit code 0 代表脚本本身成功执行（即便是 status:error 也由 JSON 描述）
    return 0


if __name__ == "__main__":
    sys.exit(main())


