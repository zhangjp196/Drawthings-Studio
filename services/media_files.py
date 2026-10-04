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

from config import MEDIA_DIR, TMP_DIR, data_dir
from config import media_url as _media_url

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


_MEDIA_NAME_RE = re.compile(r"[\w\-.]+\.(?:png|jpg|jpeg|webp|gif|mp4|mov|webm|m4v|avi)", re.I)


def referenced_media_names(db) -> set[str]:
    """扫描全部「存了媒体路径」的表，返回仍被引用的媒体文件名集合。

    这些列都是「文本里嵌一个 /media/xxx 或裸文件名」的形态（JSON 数组 / JSON 内容块 / 多行文本），
    故统一按文件名正则抽取，不依赖具体结构。跨项目/季/章节/微创作全量覆盖，类型无关。
    """
    from models import Asset, Chapter, MicroMessage, Project, Season

    # 每项是「一组同表列属性」；只 SELECT 这些文本列，不载入整行 ORM 对象
    col_groups = [
        (Project.first_image, Project.first_image_base),
        (Season.first_image, Season.first_image_base, Season.characters),
        (Chapter.media_path,),
        (MicroMessage.media_url, MicroMessage.images, MicroMessage.parts),
        (Asset.url, Asset.ref_url),
    ]
    names: set[str] = set()
    for cols in col_groups:
        for row in db.query(*cols).all():
            for v in row:
                if isinstance(v, str):
                    names.update(_MEDIA_NAME_RE.findall(v))
    return names


def find_orphan_media(db) -> tuple[list[tuple[str, int]], int, int]:
    """找出 data/media 下不被任何记录引用的文件。

    返回 (孤儿列表 [(文件名, 字节数)], 全部文件数, 全部文件总字节数)。
    只读，不删任何东西——真正的删除由 purge_orphan_media / tools/clean_media.py 执行。
    """
    if not MEDIA_DIR.is_dir():
        return [], 0, 0
    referenced = referenced_media_names(db)
    orphans: list[tuple[str, int]] = []
    count = 0
    total = 0
    for p in sorted(MEDIA_DIR.iterdir()):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        count += 1
        total += size
        if p.name not in referenced:
            orphans.append((p.name, size))
    return orphans, count, total


def purge_orphan_media(db, *, dry_run: bool = True) -> dict:
    """删除 data/media 下不被任何记录引用的孤儿文件，返回统计。

    默认 dry_run=True 只报告不删；确认后再传 dry_run=False 实际删除。
    覆盖全部历史遗留：重做/重生成覆盖掉的旧文件、生成中途失败留下的文件、已删消息的残留。
    """
    orphans, count, total = find_orphan_media(db)
    freed = sum(size for _, size in orphans)
    if not dry_run:
        for name, _ in orphans:
            try:
                (MEDIA_DIR / name).unlink()
            except OSError:
                pass
    return {
        "dry_run": dry_run,
        "orphan_count": len(orphans),
        "orphan_bytes": freed,
        "media_count": count,
        "media_bytes": total,
        "freed_bytes": 0 if dry_run else freed,
    }


def image_size(path) -> tuple[int, int]:
    """读图片**实际**尺寸；读不出返回 (0, 0)。

    写回章节宽高必须用它而不是请求尺寸：请求尺寸可能被吸附到模型原生分辨率档
    （如 qwen 系列按比例吸附到 1024/2K 档），两者不一定相同。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            return im.size
    except Exception:
        return 0, 0


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


def purge_tmp() -> int:
    """清空 data/tmp（视频抽帧等中间产物），返回释放的字节数。启动时调用。

    目录里的东西没有任何生命周期管理：进程退出即失去引用，下一次启动就可以全删。
    绝不碰 MEDIA_DIR——那里的文件是作品产物，删除必须走引用检查。
    """
    if not TMP_DIR.is_dir():
        return 0
    freed = 0
    try:
        for p in TMP_DIR.iterdir():
            try:
                if p.is_file() and not p.is_symlink():
                    freed += p.stat().st_size
                    p.unlink()
                elif p.is_dir() and not p.is_symlink():
                    import shutil

                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass
    return freed


def safe_file_base(text: str, fallback: str) -> str:
    """标题 -> 安全文件名（去掉路径/非法字符，限长）。"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (text or "").strip())[:40].strip(" ._")
    return s or fallback


def export_zip(
    title: str, fallback: str, files: list[tuple[Path, bool]], readme_label: str = "作品"
) -> tuple[str, str]:
    """把所选媒体（图/视频）打包为 ZIP：按导出顺序命名，分 images/ 与 videos/ 两个子目录。"""
    base = safe_file_base(title or fallback, fallback)
    fname = f"{base}_media.zip"
    zpath = export_dir() / fname
    n_img = sum(1 for _, v in files if not v)
    n_vid = len(files) - n_img
    readme = f"{readme_label}：{title or fallback}\n图片：{n_img} 张 · 视频：{n_vid} 个\n"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", readme.encode("utf-8"))
        for i, (p, is_video) in enumerate(files):
            sub = "videos" if is_video else "images"
            z.write(str(p), f"{sub}/{i:02d}_{p.name}")
    return str(zpath), fname


def save_images_pdf(images: list, out_path) -> None:
    """把一组已打开的 PIL 图像按顺序写成多页 PDF，并关闭它们（通用底层原语）。

    业务侧负责「选哪些图 / 什么顺序 / 文件名」，本函数只做通用编码。
    """
    if not images:
        raise ValueError("没有可导出的图片")
    try:
        images[0].save(out_path, save_all=True, append_images=images[1:], resolution=96.0)
    finally:
        for im in images:
            im.close()


def export_pdf(title: str, fallback: str, files: list[tuple[Path, bool]]) -> tuple[str, str]:
    """把所选图片按顺序拼成多页 PDF（仅图片；视频需先导出 ZIP）。"""
    base = safe_file_base(title or fallback, fallback)
    fname = f"{base}_images.pdf"
    ppath = export_dir() / fname
    imgs: list[Image.Image] = []
    for p, _ in files:
        try:
            with Image.open(p) as im:  # 及时关闭文件句柄；convert 产生独立图像
                imgs.append(im.convert("RGB"))
        except Exception:
            continue
    save_images_pdf(imgs, ppath)
    return str(ppath), fname


__all__ = [
    "cleanup_message_media",
    "is_video_url",
    "media_path_from_url",
    "save_data_uri_images",
    "referenced_media_names",
    "find_orphan_media",
    "purge_orphan_media",
    "purge_tmp",
    "export_dir",
    "safe_file_base",
    "save_images_pdf",
    "export_zip",
    "export_pdf",
]
