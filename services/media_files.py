"""媒体文件工具：落盘、清理、路径解析、ZIP / PDF 导出（微创作 / 项目可共用）。

从 `api_micro.py` 抽出的通用媒体逻辑；导出以「(文件路径, 是否视频) 有序列表」为输入，
因此不绑定任何业务模型（项目章节 / 微创作消息都能用）。
"""
import base64
import json
import re
import uuid
import zipfile
from pathlib import Path

from PIL import Image

from config import data_dir
from services.api_common import MEDIA_DIR, _media_url

_IMAGE_EXTS = ("png", "jpg", "jpeg", "webp", "gif")
_MAX_IMAGE_B64 = 8 * 1024 * 1024  # 单张用户附图上限（base64 后，约 6MB 原图）


def cleanup_message_media(msgs) -> None:
    """删除消息关联的媒体文件：助手生成媒体（media_url）+ 用户附图（images JSON 列表）。"""
    def _unlink(url: str) -> None:
        f = MEDIA_DIR / url.rsplit("/", 1)[-1].split("?")[0]
        if f.is_file() and f.resolve().is_relative_to(MEDIA_DIR.resolve()):
            f.unlink()
    for m in msgs:
        if m.media_url:
            _unlink(m.media_url)
        if m.images:
            try:
                for u in json.loads(m.images):
                    _unlink(str(u))
            except (ValueError, TypeError):
                pass


def is_video_url(url: str) -> bool:
    """按扩展名判断媒体是否为视频（媒体落盘时按实际内容定扩展名）。"""
    return bool(re.search(r"\.(mp4|mov|webm|gif)(?:\?|$)", url or "", re.I))


def media_path_from_url(url: str) -> Path | None:
    """媒体 URL（/media/xxx）-> 磁盘路径；越界或不存在返回 None。"""
    name = (url or "").rsplit("/", 1)[-1].split("?")[0]
    if not name:
        return None
    p = MEDIA_DIR / name
    try:
        if p.is_file() and p.resolve().is_relative_to(MEDIA_DIR.resolve()):
            return p
    except OSError:
        return None
    return None


def save_data_uri_images(items: list, limit: int = 4) -> list[str]:
    """把请求里的图片（data URI 列表）存到 MEDIA_DIR，返回媒体 URL 列表（默认最多 4 张）。"""
    urls: list[str] = []
    for data in [str(x) for x in (items or [])][:limit]:
        if not data.startswith("data:image/"):
            continue
        try:
            head, b64 = data.split(",", 1)
            ext = head.split("/")[-1].split(";")[0] or "png"
            if ext not in _IMAGE_EXTS:
                ext = "png"
            if len(b64) > _MAX_IMAGE_B64:
                continue
            p = MEDIA_DIR / f"mc_{uuid.uuid4().hex[:10]}.{ext}"
            p.write_bytes(base64.b64decode(b64))
            urls.append(_media_url(str(p)))
        except Exception:
            continue
    return urls


def export_dir() -> Path:
    d = Path(data_dir) / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_file_base(text: str, fallback: str) -> str:
    """标题 -> 安全文件名（去掉路径/非法字符，限长）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (text or "").strip())[:40].strip(" ._")
    return s or fallback


def export_zip(title: str, fallback: str, files: list[tuple[Path, bool]],
               readme_label: str = "作品") -> tuple[str, str]:
    """把所选媒体（图/视频）打包为 ZIP：按导出顺序命名，分 images/ 与 videos/ 两个子目录。"""
    base = safe_file_base(title or fallback, fallback)
    fname = f"{base}_media.zip"
    zpath = export_dir() / fname
    n_img = sum(1 for _, v in files if not v)
    n_vid = len(files) - n_img
    readme = (f"{readme_label}：{title or fallback}\n"
              f"图片：{n_img} 张 · 视频：{n_vid} 个\n")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme.encode("utf-8"))
        for i, (p, is_video) in enumerate(files):
            sub = "videos" if is_video else "images"
            z.write(str(p), f"{sub}/{i:02d}_{p.name}")
    return str(zpath), fname


def export_pdf(title: str, fallback: str, files: list[tuple[Path, bool]]) -> tuple[str, str]:
    """把所选图片按顺序拼成多页 PDF（仅图片；视频需先导出 ZIP）。"""
    base = safe_file_base(title or fallback, fallback)
    fname = f"{base}_images.pdf"
    ppath = export_dir() / fname
    imgs: list[Image.Image] = []
    for p, _ in files:
        try:
            with Image.open(p) as im:      # 及时关闭文件句柄；convert 产生独立图像
                imgs.append(im.convert("RGB"))
        except Exception:
            continue
    if not imgs:
        raise ValueError("没有可导出的图片")
    try:
        imgs[0].save(ppath, save_all=True, append_images=imgs[1:], resolution=96.0)
    finally:
        for im in imgs:
            im.close()
    return str(ppath), fname


__all__ = [
    "cleanup_message_media", "is_video_url", "media_path_from_url", "save_data_uri_images",
    "export_dir", "safe_file_base", "export_zip", "export_pdf",
]
