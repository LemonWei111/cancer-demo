# partners/seg-agent/server-api/integrated_wsi_pipeline.py
import os
import math
import argparse
import shutil
import subprocess
import uuid
from pathlib import Path

# 本文件: <root>/partners/seg-agent/server-api/integrated_wsi_pipeline.py
# Path(__file__).resolve().parents[3] → <root>
_ROOT = Path(__file__).resolve()
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", str(_ROOT.parents[3]))

# 统一输出目录：<root>/wsi-result/
WSI_RESULT_ROOT      = os.path.join(_PROJECT_ROOT, "wsi-result")
DEST_CONTOUR_DIR     = os.path.join(WSI_RESULT_ROOT, "brca-contour")
DEST_TRANSPARENT_DIR = os.path.join(WSI_RESULT_ROOT, "brca-transparent")
TMP_SEG_DIR          = os.path.join(_PROJECT_ROOT, "tmp", "seg-tmp")

# 启动时打一次，便于核对
print(
    f"[seg] _PROJECT_ROOT={_PROJECT_ROOT}, "
    f"WSI_RESULT_ROOT={WSI_RESULT_ROOT}",
    flush=True,
)

## 改为通过子进程调用分割脚本，避免在服务进程内直接持有GPU模型


def ensure_dirs():
    os.makedirs(DEST_CONTOUR_DIR, exist_ok=True)
    os.makedirs(DEST_TRANSPARENT_DIR, exist_ok=True)
    os.makedirs(TMP_SEG_DIR, exist_ok=True)


def _run_segmentation_subprocess(image_path: str, threshold: float = 0.5):
    """
    通过子进程调用 simple_cancer_segmentation.py 完成一次分割。
    为每次调用创建独立的输出前缀，避免并发冲突。
    """
    ensure_dirs()

    script_path = os.path.join(os.path.dirname(__file__), "simple_cancer_segmentation.py")
    if not os.path.exists(script_path):
        raise FileNotFoundError(f"分割脚本不存在: {script_path}")

    # 为本次分割生成唯一的输出前缀
    base_name = Path(image_path).stem
    unique_id = uuid.uuid4().hex
    output_base = os.path.join(TMP_SEG_DIR, f"{base_name}_{unique_id}")

    
    cmd = [
        "python",
        script_path,
        image_path,
        output_base,
    ]
    # 让子进程继承当前环境（包含 CUDA_VISIBLE_DEVICES 等）
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"子进程分割失败 (exit={result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
        )

    # 根据脚本约定，输出文件名为:
    #   <output_base>_cancer_transparent.png
    #   <output_base>_cancer_contour.png
    transparent_src = f"{output_base}_cancer_transparent.png"
    contour_src = f"{output_base}_cancer_contour.png"

    return transparent_src, contour_src


def process_image(image_path: str, threshold: float = 0.5):
    """
    处理单张图片：通过子进程进行分割，再将结果移动到固定目录。
    返回：文本描述、contour路径、transparent路径。
    """
    ensure_dirs()

    transparent_dst = None
    contour_dst = None

    transparent_src, contour_src = _run_segmentation_subprocess(image_path, threshold=threshold)

    # 将结果移动到统一目录，供后续智能体使用
    if contour_src and os.path.exists(contour_src):
        contour_dst = os.path.join(DEST_CONTOUR_DIR, os.path.basename(contour_src))
        shutil.move(contour_src, contour_dst)

    if transparent_src and os.path.exists(transparent_src):
        transparent_dst = os.path.join(DEST_TRANSPARENT_DIR, os.path.basename(transparent_src))
        shutil.move(transparent_src, transparent_dst)

    return {
        "image": image_path,
        "label": "分割完成",
        "score": None,
        "contour_path": contour_dst,
        "transparent_path": transparent_dst,
    }


def process_entry(input_path: str):
    """
    输入可以是单张图片或文件夹。
    返回：
      - 单图：一个结果字典
      - 文件夹：文件名 -> 结果字典 的映射
    """
    threshold = 0.5
    
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"输入路径不存在: {input_path}")

    if os.path.isfile(input_path):
        return process_image(input_path, threshold)

    if os.path.isdir(input_path):
        image_extensions = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif')
        images = []
        p = Path(input_path)
        for ext in image_extensions:
            images.extend(p.glob(f"*{ext}"))
            images.extend(p.glob(f"*{ext.upper()}"))

        results = {}
        for img_path in sorted({str(x) for x in images}):
            res = process_image(img_path, threshold)
            results[os.path.basename(img_path)] = res
        return results

    raise ValueError(f"输入路径既不是文件也不是文件夹: {input_path}")


def format_output_text(result):
    """
    将结果（单图或批量）格式化为文本输出。
    """
    def one_line(r):
        parts = [
            f"切片: {os.path.basename(r['image'])}",
            f"状态: {r.get('label', '分割完成')}",
        ]
        if r.get('contour_path'):
            parts.append(f"contour: {r['contour_path']}")
        if r.get('transparent_path'):
            parts.append(f"transparent: {r['transparent_path']}")
        return " | ".join(parts)

    if isinstance(result, dict) and 'image' in result:
        return one_line(result)
    if isinstance(result, dict):
        lines = []
        for name, r in result.items():
            lines.append(one_line(r))
        return "\n".join(lines)
    return str(result)


if __name__ == '__main__':
    result = process_entry("/home/user/weijing/cancer-demo/test_batch_images/3.jpg")
    print(format_output_text(result))


