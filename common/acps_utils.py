# common/acps_utils.py
"""公共工具函数：base64 落盘、FileDataItem 处理、图片压缩。"""
import base64
import io
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from acps_sdk.aip import FileDataItem
from PIL import Image

from acps_config import ACPS_UPLOAD_DIR

logger = logging.getLogger("AcpsUtils")


# ---------------------------------------------------------------------------
# base64 → 本地文件
# ---------------------------------------------------------------------------
def b64_to_local_file(b64_data: str, mime: str = "image/png") -> Optional[str]:
    try:
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        raw = base64.b64decode(b64_data)

        ext = ".png"
        if "jpeg" in mime or "jpg" in mime:
            ext = ".jpg"
        elif "tiff" in mime:
            ext = ".tiff"
        elif "svs" in mime:
            ext = ".svs"

        upload_dir = str(ACPS_UPLOAD_DIR)
        os.makedirs(upload_dir, exist_ok=True)
        save_path = os.path.join(upload_dir, f"acps_in_{uuid.uuid4().hex}{ext}")
        with open(save_path, "wb") as fp:
            fp.write(raw)
        logger.info("已落盘输入文件: %s (%d bytes)", save_path, len(raw))
        return save_path
    except Exception as exc:
        logger.error("base64 落盘失败: %s", exc)
        return None


_b64_to_local_file = b64_to_local_file


def file_item_to_path(item: FileDataItem, prefer_uri: bool = False) -> Optional[str]:
    """
    把 FileDataItem 转成本地文件路径。
    - prefer_uri=False（默认）：优先 bytes（落盘），fallback 到 uri
    - prefer_uri=True：优先 uri（本地路径），fallback 到 bytes
    """
    b64 = getattr(item, "bytes", None)
    mime = getattr(item, "mimeType", None) or "application/octet-stream"
    uri = getattr(item, "uri", None)

    if prefer_uri:
        if uri and os.path.isabs(uri) and os.path.isfile(uri):
            return uri
        if b64:
            return b64_to_local_file(b64, mime)
    else:
        if b64:
            local = b64_to_local_file(b64, mime)
            if local:
                return local
        if uri and os.path.isabs(uri) and os.path.isfile(uri):
            return uri
    return None


# ---------------------------------------------------------------------------
# 图片压缩
# ---------------------------------------------------------------------------
def compress_image_to_b64(
    path: str,
    max_size: int = 800,
    quality: int = 85,
) -> tuple[str, str]:
    """
    压缩图片。
    - 有 alpha → PNG（保透明）
    - 无 alpha → JPEG（有损，体积小）
    返回 (b64, mime)
    """
    img = Image.open(path)

    if max(img.size) > max_size:
        ratio = max_size / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info
    )

    buf = io.BytesIO()
    if has_alpha:
        img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"

    return base64.b64encode(buf.getvalue()).decode("ascii"), mime

def pack_files_as_dataitems(files: list) -> list:
    """
    files[] 每个元素：
      - bytes_b64: 已压缩 base64（优先）
      - path:      本地文件路径（若 bytes_b64 未给，读文件）
      - uri:       本地路径字符串（透传下游）
    """
    items: list = []
    for f in files or []:
        name = f.get("name") or "file"
        mime = f.get("mime") or "application/octet-stream"
        uri = f.get("uri", "") or ""
        b64 = f.get("bytes_b64")
        path = f.get("path")

        if b64 is None and path is None:
            if not uri:
                continue
            items.append(FileDataItem(name=name, mimeType=mime, uri=uri, bytes=None))
            continue

        if not b64 and path and os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    b64 = base64.b64encode(fh.read()).decode("ascii")
            except Exception as exc:
                logger.warning("读取文件失败 %s: %s", path, exc)

        if not b64:
            if uri:
                items.append(FileDataItem(name=name, mimeType=mime, uri=uri, bytes=None))
            continue

        items.append(FileDataItem(name=name, mimeType=mime, uri=uri, bytes=b64))
    return items

# 唯一权威的 mime → 后缀映射
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/tiff": ".tiff",
    "text/csv":   ".csv",
    "application/octet-stream": "",   # 不加后缀
}


def ext_for_mime(mime: str) -> str:
    """根据 mime 返回规范后缀（含点）。未识别返回空串。"""
    if not mime:
        return ""
    return _MIME_TO_EXT.get(mime.lower(), "")


def force_ext(name: str, mime: str) -> str:
    """
    强制把 name 的后缀改成与 mime 一致。
    - 未识别 mime → 保留原名
    - application/octet-stream → 去掉后缀
    """
    if not name:
        name = "result"
    ext = ext_for_mime(mime)
    if mime and mime.lower() not in _MIME_TO_EXT:
        return name  # 未知 mime，不动
    base, _old = os.path.splitext(name)
    return f"{base}{ext}" if ext else base

# 唯一权威的 mime → 后缀映射
_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg":  ".jpg",
    "image/png":  ".png",
    "image/tiff": ".tiff",
    "text/csv":   ".csv",
    "application/octet-stream": "",   # 不加后缀
}


def ext_for_mime(mime: str) -> str:
    """根据 mime 返回规范后缀（含点）。未识别返回空串。"""
    if not mime:
        return ""
    return _MIME_TO_EXT.get(mime.lower(), "")


def force_ext(name: str, mime: str) -> str:
    """
    强制把 name 的后缀改成与 mime 一致。
    - 未识别 mime → 保留原名
    - application/octet-stream → 去掉后缀
    """
    if not name:
        name = "result"
    ext = ext_for_mime(mime)
    if mime and mime.lower() not in _MIME_TO_EXT:
        return name  # 未知 mime，不动
    base, _old = os.path.splitext(name)
    return f"{base}{ext}" if ext else base